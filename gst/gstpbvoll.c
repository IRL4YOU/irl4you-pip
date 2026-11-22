/* IRL4YOU BOX: Verpixeln des ganzen Bildes (GStreamer-Baustein pbpixelfull). MIT-Lizenz, siehe LICENSE. */
#ifdef HAVE_CONFIG_H
#include "config.h"
#endif
#include <string.h>
#include <stdlib.h>
#include <pthread.h>
#include <gst/gst.h>
#include <gst/base/gstbasetransform.h>
#include <gst/video/video.h>

GST_DEBUG_CATEGORY_STATIC(pbfull_debug);
#define GST_CAT_DEFAULT pbfull_debug

typedef struct {
  GstBaseTransform parent;
  GstVideoInfo info;
  gboolean have_info;
  gboolean enabled;
  guint cells, noise, grain, pct, threads;
  guint64 frames;
  guint32 rng;
} PbPixelFull;
typedef struct { GstBaseTransformClass parent_class; } PbPixelFullClass;
G_DEFINE_TYPE(PbPixelFull, pb_pixel_full, GST_TYPE_BASE_TRANSFORM)

enum { PF_0, PF_ENABLED, PF_CELLS, PF_NOISE, PF_GRAIN, PF_PCT, PF_FRAMES, PF_THREADS };

static GstStaticPadTemplate pf_sink = GST_STATIC_PAD_TEMPLATE("sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));
static GstStaticPadTemplate pf_src = GST_STATIC_PAD_TEMPLATE("src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));

static gint pf_cell_size(gint shortside, gint cells) {
  static const gint stufen[] = {4, 6, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256};
  gint roh = (shortside + cells - 1) / cells;
  if (roh < 4) roh = 4;
  gint best = stufen[0];
  for (guint i = 0; i < G_N_ELEMENTS(stufen); i++) {
    const gint d = stufen[i] > roh ? stufen[i] - roh : roh - stufen[i];
    const gint db = best > roh ? best - roh : roh - best;
    if (d < db) best = stufen[i];
  }
  return best;
}

static guint32 pf_rnd(guint32 *st) {
  guint32 x = *st ? *st : 2463534242u;
  x ^= x << 13; x ^= x >> 17; x ^= x << 5;
  *st = x;
  return x;
}

/* Das Bild wird nur geschrieben, nie Punkt für Punkt gelesen (der Bildspeicher ist zum Lesen langsam).
   Es wird in waagerechte Streifen aus ganzen Kästchenreihen geteilt, jeder Streifen läuft auf einem eigenen Kern. */
typedef struct {
  guint8 *py, *puv;
  gint sy, suv, fw, fh, cs, amp, grain, pct, r0, r1;
  const gint8 *tab;
  guint32 rng;
} PfJob;

static void *pf_band(void *arg) {
  PfJob *j = (PfJob *) arg;
  guint8 rowbuf[8192], avgs[2048], noisy[2048];
  const gint cs = j->cs, fw = j->fw, fh = j->fh, sy = j->sy, suv = j->suv;
  for (gint ry = j->r0; ry < j->r1; ry++) {
    const gint gy = ry * cs;
    const gint ch = (gy + cs < fh) ? cs : fh - gy;
    gint cxi = 0;
    for (gint gx = 0; gx < fw; gx += cs, cxi++) {
      const gint cw = (gx + cs < fw) ? cs : fw - gx;
      guint32 sum = 0;
      for (gint k = 0; k < 4; k++) {
        const guint8 *row = j->py + (gsize) (gy + (2 * k + 1) * ch / 8) * sy + gx;
        for (gint x = 0; x < 4; x++) sum += row[(2 * x + 1) * cw / 8];
      }
      avgs[cxi] = (guint8) (sum / 16);
      noisy[cxi] = (guint8) (((cxi * 7 + ry * 13 + ry * cxi) % 100) < j->pct);
      const gint cwc = cw / 2, chc = (ch + 1) / 2;
      if (cwc < 1) continue;
      guint32 su = 0, sv = 0;
      for (gint k = 0; k < 2; k++) {
        const guint8 *row = j->puv + (gsize) (gy / 2 + (2 * k + 1) * chc / 4) * suv + gx;
        for (gint x = 0; x < 2; x++) { su += row[2 * ((2 * x + 1) * cwc / 4)]; sv += row[2 * ((2 * x + 1) * cwc / 4) + 1]; }
      }
      const guint8 au = (guint8) (su / 4), av = (guint8) (sv / 4);
      for (gint y = 0; y < chc; y++) {
        guint8 *row = j->puv + (gsize) (gy / 2 + y) * suv + gx;
        for (gint x = 0; x < cwc; x++) { row[2 * x] = au; row[2 * x + 1] = av; }
      }
    }
    gint off = 0;
    for (gint y = 0; y < ch; y++) {
      if (j->amp > 0) {
        if (y % j->grain == 0) off = (gint) ((pf_rnd(&j->rng) >> 8) & 4095);
        for (gint x = 0; x < fw; x++) {
          const gint v = (gint) avgs[x / cs] + (noisy[x / cs] ? j->tab[off + ((x / j->grain) & 4095)] : 0);
          rowbuf[x] = (guint8) (v < 0 ? 0 : (v > 255 ? 255 : v));
        }
      } else {
        for (gint x = 0; x < fw; x++) rowbuf[x] = avgs[x / cs];
      }
      memcpy(j->py + (gsize) (gy + y) * sy, rowbuf, (gsize) fw);
    }
  }
  return NULL;
}

static void pf_frame(guint8 *py, gint sy, guint8 *puv, gint suv, gint fw, gint fh, gint cells, gint amp, gint grain, gint pct, gint nthreads, guint32 *st) {
  const gint cs = pf_cell_size(fw < fh ? fw : fh, cells);
  static gint8 tab[8192];
  const gint nrows = (fh + cs - 1) / cs;
  if ((fw + cs - 1) / cs > 2048 || fw > 8192) return;
  if (grain < 1) grain = 1;
  if (nthreads < 1) nthreads = 1;
  if (nthreads > 8) nthreads = 8;
  if (nthreads > nrows) nthreads = nrows;
  if (amp > 0) {
    const gint span = 2 * amp + 1;
    for (gint i = 0; i < 8192; i++) tab[i] = (gint8) ((gint) (pf_rnd(st) % (guint32) span) - amp);
  }
  PfJob jobs[8];
  pthread_t th[8];
  gboolean started[8] = {FALSE};
  for (gint i = 0; i < nthreads; i++) {
    jobs[i] = (PfJob) { py, puv, sy, suv, fw, fh, cs, amp, grain, pct, i * nrows / nthreads, (i + 1) * nrows / nthreads, tab, pf_rnd(st) };
  }
  for (gint i = 1; i < nthreads; i++) started[i] = (pthread_create(&th[i], NULL, pf_band, &jobs[i]) == 0);
  pf_band(&jobs[0]);
  for (gint i = 1; i < nthreads; i++) {
    if (started[i]) pthread_join(th[i], NULL);
    else pf_band(&jobs[i]);
  }
}

static void pf_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbPixelFull *s = (PbPixelFull *) o;
  switch (id) {
    case PF_ENABLED: s->enabled = g_value_get_boolean(v); break;
    case PF_CELLS: s->cells = g_value_get_uint(v); break;
    case PF_NOISE: s->noise = g_value_get_uint(v); break;
    case PF_GRAIN: s->grain = g_value_get_uint(v); break;
    case PF_PCT: s->pct = g_value_get_uint(v); break;
    case PF_THREADS: s->threads = g_value_get_uint(v); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}
static void pf_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbPixelFull *s = (PbPixelFull *) o;
  switch (id) {
    case PF_ENABLED: g_value_set_boolean(v, s->enabled); break;
    case PF_CELLS: g_value_set_uint(v, s->cells); break;
    case PF_NOISE: g_value_set_uint(v, s->noise); break;
    case PF_GRAIN: g_value_set_uint(v, s->grain); break;
    case PF_PCT: g_value_set_uint(v, s->pct); break;
    case PF_THREADS: g_value_set_uint(v, s->threads); break;
    case PF_FRAMES: g_value_set_uint64(v, s->frames); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

static gboolean pf_set_caps(GstBaseTransform *t, GstCaps *in, GstCaps *out) {
  PbPixelFull *s = (PbPixelFull *) t;
  s->have_info = gst_video_info_from_caps(&s->info, in);
  return TRUE;
}

static GstFlowReturn pf_transform_ip(GstBaseTransform *t, GstBuffer *buf) {
  PbPixelFull *s = (PbPixelFull *) t;
  s->frames++;
  if (!s->enabled || !s->have_info) return GST_FLOW_OK;
  GstVideoFrame fr;
  if (!gst_video_frame_map(&fr, &s->info, buf, GST_MAP_READ | GST_MAP_WRITE)) return GST_FLOW_ERROR;
  pf_frame(GST_VIDEO_FRAME_PLANE_DATA(&fr, 0), GST_VIDEO_FRAME_PLANE_STRIDE(&fr, 0), GST_VIDEO_FRAME_PLANE_DATA(&fr, 1), GST_VIDEO_FRAME_PLANE_STRIDE(&fr, 1),
           GST_VIDEO_FRAME_WIDTH(&fr), GST_VIDEO_FRAME_HEIGHT(&fr), (gint) s->cells, (gint) s->noise, (gint) s->grain, (gint) s->pct, (gint) s->threads, &s->rng);
  gst_video_frame_unmap(&fr);
  return GST_FLOW_OK;
}

static void pb_pixel_full_class_init(PbPixelFullClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  GstBaseTransformClass *bc = GST_BASE_TRANSFORM_CLASS(klass);
  oc->set_property = pf_set_property;
  oc->get_property = pf_get_property;
  g_object_class_install_property(oc, PF_ENABLED, g_param_spec_boolean("enabled", "Aktiv", "Verpixeln an/aus", TRUE, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  g_object_class_install_property(oc, PF_CELLS, g_param_spec_uint("cells", "Kästchen", "Kästchen an der kurzen Bildseite (weniger = gröber)", 2, 200, 17, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PF_NOISE, g_param_spec_uint("noise", "Rauschen", "Stärke", 0, 60, 1, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PF_GRAIN, g_param_spec_uint("grain", "Korn", "Größe in Bildpunkten", 1, 16, 1, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PF_PCT, g_param_spec_uint("pct", "Anteil", "Anteil der Kästchen in Prozent", 0, 100, 17, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PF_THREADS, g_param_spec_uint("threads", "Kerne", "Anzahl der Streifen/Kerne", 1, 8, 1, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PF_FRAMES, g_param_spec_uint64("frames", "Bilder", "durchgelaufene Bilder", 0, G_MAXUINT64, 0, G_PARAM_READABLE | G_PARAM_STATIC_STRINGS));
  gst_element_class_set_static_metadata(ec, "IRL4YOU Verpixeln: ganzes Bild", "Filter/Video", "Verpixelt das ganze Bild", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &pf_sink);
  gst_element_class_add_static_pad_template(ec, &pf_src);
  bc->set_caps = pf_set_caps;
  bc->transform_ip = pf_transform_ip;
  bc->transform_ip_on_passthrough = TRUE;
}
static void pb_pixel_full_init(PbPixelFull *s) {
  s->have_info = FALSE; s->enabled = TRUE; s->cells = 17; s->noise = 1; s->grain = 1; s->pct = 17; s->threads = 1; s->frames = 0; s->rng = 2463534242u;
  gst_base_transform_set_in_place(GST_BASE_TRANSFORM(s), TRUE);
  gst_base_transform_set_passthrough(GST_BASE_TRANSFORM(s), FALSE);
}

static gboolean plugin_init(GstPlugin *plugin) {
  GST_DEBUG_CATEGORY_INIT(pbfull_debug, "pbfull", 0, "IRL4YOU Verpixeln ganzes Bild");
  return gst_element_register(plugin, "pbpixelfull", GST_RANK_NONE, pb_pixel_full_get_type());
}

#ifndef PACKAGE
#define PACKAGE "irl4you-pip"
#endif
GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, pbvoll,
                  "IRL4YOU BOX Verpixeln ganzes Bild",
                  plugin_init, "0.1", "MIT/X11", "irl4you-pip", "https://github.com/IRL4YOU/irl4you-pip")
