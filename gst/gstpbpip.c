/* IRL4YOU PIP: Bild-in-Bild-Baustein für GStreamer (NV12).
 *
 * Zwei Elemente, die sich einen kleinen Zwischenspeicher teilen (Eigenschaft slot 0 oder 1,
 * so sind bis zu zwei kleine Bilder gleichzeitig möglich):
 *
 *   pbpipsink  nimmt das KLEINE Bild entgegen (schon vom Hardware-Decoder auf z. B. 480x270
 *              verkleinert) und legt es in einem Ringspeicher ab.
 *   pbpipmix   steht im Hauptbild-Pfad und schreibt pro Hauptbild das nächste kleine Bild an
 *              die gewählte Ecke HINEIN. Das große Bild wird nie gelesen.
 *
 * Warum so: Auf dem RK3588 ist das Lesen von Decoder-Bildern durch die CPU extrem langsam
 * (gemessen: 1080p-Umwandlung ca. 9 Bilder/s). Schreiben in diesen Speicher ist schnell.
 * Ein Standard-Mischer (compositor) muss das große Bild lesen und schafft deshalb kein 30 fps.
 *
 * Der Ringspeicher gleicht Schübe des kleinen Bildes aus (Vorlauf von wenigen Bildern, zu alte
 * Bilder werden verworfen). Bleibt das kleine Bild länger als 2 Sekunden aus, wird nichts mehr
 * eingeblendet (statt eines eingefrorenen Bildes).
 *
 * Drittes Element pbctl: liest alle 0,3 s eine kleine Datei (bis zu drei Zahlen in Millisekunden: Hauptbild,
 * kleines Bild 1, kleines Bild 2) und stellt damit die Wartezeit (min-threshold-time) benannter queue-Elemente um.
 * So lassen sich die Verzögerungen im laufenden Betrieb ändern, ohne die Sendekette neu zu starten.
 *
 * MIT-Lizenz, Copyright (c) 2026 IRL4YOU. Eigene Umsetzung.
 */
#ifdef HAVE_CONFIG_H
#include "config.h"
#endif
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <gst/gst.h>
#include <gst/base/gstbasesink.h>
#include <gst/video/video.h>
#include <gst/video/gstvideofilter.h>

GST_DEBUG_CATEGORY_STATIC(pbpip_debug);
#define GST_CAT_DEFAULT pbpip_debug

#define RING_CAP 12              /* höchstens so viele kleine Bilder vorhalten */
#define PREBUFFER 3              /* Vorlauf, bevor das erste Bild gezeigt wird */
#define NSLOTS 3                 /* so viele kleine Bilder gleichzeitig */
#define STALE_US (2 * G_USEC_PER_SEC)

/* ------------------------------------------------------------------ gemeinsamer Speicher */

static GMutex ring_lock;
typedef struct {
  guint8 *slot[RING_CAP];
  gsize slot_size;
  guint rd, count;               /* Lesepunkt, Anzahl belegter Plätze */
  gint w, h;                     /* Größe des kleinen Bildes (dicht gepackt: Stride = w) */
  gboolean started;
  gint64 last_push;              /* Zeitpunkt des letzten kleinen Bildes */
  guint8 *cur;                   /* zuletzt gezeigtes Bild */
  gboolean have_cur;
  guint64 pushed, dropped, shown, repeated;
} Ring;
static Ring rings[NSLOTS];

static void ring_reset_locked(Ring *ring) {
  ring->rd = ring->count = 0;
  ring->started = FALSE;
  ring->have_cur = FALSE;
}

static void ring_resize_locked(Ring *ring, gint w, gint h) {
  gsize need = (gsize) w * h * 3 / 2;
  if (need != ring->slot_size) {
    for (guint i = 0; i < RING_CAP; i++) {
      g_free(ring->slot[i]);
      ring->slot[i] = g_malloc(need);
    }
    g_free(ring->cur);
    ring->cur = g_malloc(need);
    ring->slot_size = need;
  }
  ring->w = w;
  ring->h = h;
  ring_reset_locked(ring);
}

/* ------------------------------------------------------------------ pbpipsink */

typedef struct { GstBaseSink parent; GstVideoInfo info; gboolean have_info; guint slot; } PbPipSink;
typedef struct { GstBaseSinkClass parent_class; } PbPipSinkClass;
G_DEFINE_TYPE(PbPipSink, pb_pip_sink, GST_TYPE_BASE_SINK)

static GstStaticPadTemplate sink_tmpl = GST_STATIC_PAD_TEMPLATE(
    "sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));

static gboolean pb_sink_set_caps(GstBaseSink *bs, GstCaps *caps) {
  PbPipSink *self = (PbPipSink *) bs;
  if (!gst_video_info_from_caps(&self->info, caps))
    return FALSE;
  Ring *ring = &rings[self->slot];
  g_mutex_lock(&ring_lock);
  ring_resize_locked(ring, GST_VIDEO_INFO_WIDTH(&self->info), GST_VIDEO_INFO_HEIGHT(&self->info));
  g_mutex_unlock(&ring_lock);
  self->have_info = TRUE;
  GST_INFO_OBJECT(self, "kleines Bild %dx%d", ring->w, ring->h);
  return TRUE;
}

static GstFlowReturn pb_sink_render(GstBaseSink *bs, GstBuffer *buf) {
  PbPipSink *self = (PbPipSink *) bs;
  GstVideoFrame f;
  if (!self->have_info || !gst_video_frame_map(&f, &self->info, buf, GST_MAP_READ))
    return GST_FLOW_OK;
  Ring *ring = &rings[self->slot];
  const gint w = ring->w, h = ring->h;
  g_mutex_lock(&ring_lock);
  if (ring->count == RING_CAP) {                    /* voll: ältestes verwerfen */
    ring->rd = (ring->rd + 1) % RING_CAP;
    ring->count--;
    ring->dropped++;
  }
  guint8 *dst = ring->slot[(ring->rd + ring->count) % RING_CAP];
  const guint8 *y = GST_VIDEO_FRAME_PLANE_DATA(&f, 0), *uv = GST_VIDEO_FRAME_PLANE_DATA(&f, 1);
  const gint sy = GST_VIDEO_FRAME_PLANE_STRIDE(&f, 0), suv = GST_VIDEO_FRAME_PLANE_STRIDE(&f, 1);
  for (gint r = 0; r < h; r++)
    memcpy(dst + (gsize) r * w, y + (gsize) r * sy, w);
  for (gint r = 0; r < h / 2; r++)
    memcpy(dst + (gsize) w * h + (gsize) r * w, uv + (gsize) r * suv, w);
  ring->count++;
  ring->pushed++;
  ring->last_push = g_get_monotonic_time();
  g_mutex_unlock(&ring_lock);
  gst_video_frame_unmap(&f);
  return GST_FLOW_OK;
}

static gboolean pb_sink_stop(GstBaseSink *bs) {
  PbPipSink *self = (PbPipSink *) bs;
  g_mutex_lock(&ring_lock);
  ring_reset_locked(&rings[self->slot]);
  g_mutex_unlock(&ring_lock);
  return TRUE;
}

static void pb_sink_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbPipSink *self = (PbPipSink *) o;
  if (id == 1) self->slot = g_value_get_uint(v) % NSLOTS;
  else G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
}

static void pb_sink_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbPipSink *self = (PbPipSink *) o;
  if (id == 1) g_value_set_uint(v, self->slot);
  else G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
}

static void pb_pip_sink_class_init(PbPipSinkClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  oc->set_property = pb_sink_set_property;
  oc->get_property = pb_sink_get_property;
  g_object_class_install_property(oc, 1,
      g_param_spec_uint("slot", "Platz", "welches kleine Bild (0 bis 2)", 0, NSLOTS - 1, 0,
                        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  GstBaseSinkClass *bc = GST_BASE_SINK_CLASS(klass);
  gst_element_class_set_static_metadata(ec, "IRL4YOU PiP Eingang", "Sink/Video",
      "Nimmt das kleine Bild für Bild-in-Bild entgegen", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &sink_tmpl);
  bc->set_caps = pb_sink_set_caps;
  bc->render = pb_sink_render;
  bc->stop = pb_sink_stop;
}

static void pb_pip_sink_init(PbPipSink *self) {
  gst_base_sink_set_sync(GST_BASE_SINK(self), FALSE);   /* sofort, ohne Taktabgleich */
  gst_base_sink_set_async_enabled(GST_BASE_SINK(self), FALSE);
  self->have_info = FALSE;
}

/* ------------------------------------------------------------------ pbpipmix */

/* PB_POS_BEGIN  (wird von tools/test_corner_pos.py einzeln übersetzt und geprüft) */
/* Position des kleinen Bildes: 0 oben links, 1 oben rechts, 2 unten links, 3 unten rechts, 4 unten Mitte,
 * 5 frei: fx/fy in Promille des Verschiebewegs (0 = Rand links/oben, 1000 = Rand rechts/unten), das Bild bleibt immer im Bild */
static void pb_pos(guint corner, gint mw, gint mh, gint pw, gint ph, gint margin, gint fx, gint fy, gint *x, gint *y) {
  if (corner == 5u) {
    if (fx < 0) fx = 0;
    if (fx > 1000) fx = 1000;
    if (fy < 0) fy = 0;
    if (fy > 1000) fy = 1000;
    *x = (gint) (((gint64) (mw - pw) * fx) / 1000);
    *y = (gint) (((gint64) (mh - ph) * fy) / 1000);
    if (*x < 0) *x = 0;
    if (*y < 0) *y = 0;
  } else if (corner == 4u) {
    *x = (mw - pw) / 2;
    *y = mh - ph - margin;
  } else {
    *x = (corner & 1u) ? mw - pw - margin : margin;
    *y = (corner & 2u) ? mh - ph - margin : margin;
  }
}
/* PB_POS_END */

typedef struct { GstVideoFilter parent; guint corner; guint slot; gint slot2; guint corner2; gint slot3; guint corner3; guint fx, fy, fx2, fy2, fx3, fy3; } PbPipMix;
typedef struct { GstVideoFilterClass parent_class; } PbPipMixClass;
G_DEFINE_TYPE(PbPipMix, pb_pip_mix, GST_TYPE_VIDEO_FILTER)

enum { PROP_0, PROP_CORNER, PROP_WIDTH_PCT, PROP_SLOT, PROP_SLOT2, PROP_CORNER2, PROP_SLOT3, PROP_CORNER3, PROP_X, PROP_Y, PROP_X2, PROP_Y2, PROP_X3, PROP_Y3 };

static GstStaticPadTemplate mix_sink = GST_STATIC_PAD_TEMPLATE(
    "sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));
static GstStaticPadTemplate mix_src = GST_STATIC_PAD_TEMPLATE(
    "src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));

static void pb_mix_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbPipMix *self = (PbPipMix *) o;
  switch (id) {
    case PROP_CORNER: g_atomic_int_set(&self->corner, MIN(g_value_get_uint(v), 5u)); break;
    case PROP_WIDTH_PCT: break;      /* nur zur Dokumentation; die Größe liefert der Decoder */
    case PROP_SLOT: self->slot = g_value_get_uint(v) % NSLOTS; break;
    case PROP_SLOT2: self->slot2 = g_value_get_int(v) < 0 ? -1 : g_value_get_int(v) % NSLOTS; break;
    case PROP_CORNER2: g_atomic_int_set(&self->corner2, MIN(g_value_get_uint(v), 5u)); break;
    case PROP_X: g_atomic_int_set(&self->fx, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_Y: g_atomic_int_set(&self->fy, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_X2: g_atomic_int_set(&self->fx2, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_Y2: g_atomic_int_set(&self->fy2, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_X3: g_atomic_int_set(&self->fx3, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_Y3: g_atomic_int_set(&self->fy3, MIN(g_value_get_uint(v), 1000u)); break;
    case PROP_SLOT3: self->slot3 = g_value_get_int(v) < 0 ? -1 : g_value_get_int(v) % NSLOTS; break;
    case PROP_CORNER3: g_atomic_int_set(&self->corner3, MIN(g_value_get_uint(v), 5u)); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

static void pb_mix_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbPipMix *self = (PbPipMix *) o;
  switch (id) {
    case PROP_CORNER: g_value_set_uint(v, g_atomic_int_get(&self->corner)); break;
    case PROP_WIDTH_PCT: g_value_set_uint(v, 0); break;
    case PROP_SLOT: g_value_set_uint(v, self->slot); break;
    case PROP_SLOT2: g_value_set_int(v, self->slot2); break;
    case PROP_CORNER2: g_value_set_uint(v, g_atomic_int_get(&self->corner2)); break;
    case PROP_X: g_value_set_uint(v, g_atomic_int_get(&self->fx)); break;
    case PROP_Y: g_value_set_uint(v, g_atomic_int_get(&self->fy)); break;
    case PROP_X2: g_value_set_uint(v, g_atomic_int_get(&self->fx2)); break;
    case PROP_Y2: g_value_set_uint(v, g_atomic_int_get(&self->fy2)); break;
    case PROP_X3: g_value_set_uint(v, g_atomic_int_get(&self->fx3)); break;
    case PROP_Y3: g_value_set_uint(v, g_atomic_int_get(&self->fy3)); break;
    case PROP_SLOT3: g_value_set_int(v, self->slot3); break;
    case PROP_CORNER3: g_value_set_uint(v, g_atomic_int_get(&self->corner3)); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

/* Zeichnet das kleine Bild aus Platz "slot" in das Hauptbild. Aufrufer hält ring_lock. */
static void pb_mix_draw(GstVideoFrame *frame, guint slot, guint corner, gint fx, gint fy) {
  Ring *ring = &rings[slot];
  if (ring->w <= 0 || ring->slot_size == 0)
    return;
  if (!ring->started && ring->count >= PREBUFFER)
    ring->started = TRUE;
  while (ring->count > PREBUFFER + 3) {             /* zu viel Vorrat: aufholen */
    ring->rd = (ring->rd + 1) % RING_CAP;
    ring->count--;
    ring->dropped++;
  }
  if (ring->started && ring->count > 0) {
    memcpy(ring->cur, ring->slot[ring->rd], ring->slot_size);
    ring->rd = (ring->rd + 1) % RING_CAP;
    ring->count--;
    ring->have_cur = TRUE;
    ring->shown++;
  } else if (ring->have_cur) {
    ring->repeated++;
  }
  const gint64 age = g_get_monotonic_time() - ring->last_push;
  const gboolean draw = ring->have_cur && age < STALE_US;
  const gint pw = ring->w, ph = ring->h;
  const gint mw = GST_VIDEO_FRAME_WIDTH(frame), mh = GST_VIDEO_FRAME_HEIGHT(frame);
  if (draw && pw <= mw && ph <= mh) {
    const gint margin = (mw / 60) & ~1;            /* ca. 2 % der Breite, gerade */
    gint x, y;
    pb_pos(corner, mw, mh, pw, ph, margin, fx, fy, &x, &y);
    x &= ~1;
    y &= ~1;
    if (x >= 0 && y >= 0) {
      guint8 *dy = GST_VIDEO_FRAME_PLANE_DATA(frame, 0), *duv = GST_VIDEO_FRAME_PLANE_DATA(frame, 1);
      const gint sy = GST_VIDEO_FRAME_PLANE_STRIDE(frame, 0), suv = GST_VIDEO_FRAME_PLANE_STRIDE(frame, 1);
      for (gint r = 0; r < ph; r++)
        memcpy(dy + (gsize) (y + r) * sy + x, ring->cur + (gsize) r * pw, pw);
      for (gint r = 0; r < ph / 2; r++)
        memcpy(duv + (gsize) (y / 2 + r) * suv + x, ring->cur + (gsize) pw * ph + (gsize) r * pw, pw);
    }
  }
}

static GstFlowReturn pb_mix_transform_ip(GstVideoFilter *filter, GstVideoFrame *frame) {
  PbPipMix *self = (PbPipMix *) filter;
  g_mutex_lock(&ring_lock);
  pb_mix_draw(frame, self->slot, g_atomic_int_get(&self->corner), g_atomic_int_get(&self->fx), g_atomic_int_get(&self->fy));
  if (self->slot2 >= 0)                              /* zweites kleines Bild im selben Durchgang (nur ein Mapping des Hauptbilds) */
    pb_mix_draw(frame, (guint) self->slot2, g_atomic_int_get(&self->corner2), g_atomic_int_get(&self->fx2), g_atomic_int_get(&self->fy2));
  if (self->slot3 >= 0)
    pb_mix_draw(frame, (guint) self->slot3, g_atomic_int_get(&self->corner3), g_atomic_int_get(&self->fx3), g_atomic_int_get(&self->fy3));
  g_mutex_unlock(&ring_lock);
  return GST_FLOW_OK;
}

static void pb_pip_mix_class_init(PbPipMixClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  GstVideoFilterClass *vc = GST_VIDEO_FILTER_CLASS(klass);
  oc->set_property = pb_mix_set_property;
  oc->get_property = pb_mix_get_property;
  g_object_class_install_property(oc, PROP_CORNER,
      g_param_spec_uint("corner", "Ecke", "0 oben links, 1 oben rechts, 2 unten links, 3 unten rechts, 4 unten Mitte, 5 frei (x/y)",
                        0, 5, 3, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  g_object_class_install_property(oc, PROP_WIDTH_PCT,
      g_param_spec_uint("width-pct", "Breite in Prozent", "nur zur Information (Größe liefert der Decoder)",
                        0, 100, 0, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PROP_SLOT,
      g_param_spec_uint("slot", "Platz", "welches kleine Bild (0 bis 2)", 0, NSLOTS - 1, 0,
                        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, PROP_SLOT2,
      g_param_spec_int("slot2", "Zweiter Platz", "zweites kleines Bild im selben Durchgang (-1 = keins)", -1, NSLOTS - 1, -1,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, PROP_CORNER2,
      g_param_spec_uint("corner2", "Ecke 2", "Ecke des zweiten kleinen Bildes", 0, 5, 2,
                        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  {
    static const struct { guint id; const gchar *name; const gchar *nick; } fp[] = {
      {PROP_X, "x", "X"}, {PROP_Y, "y", "Y"}, {PROP_X2, "x2", "X2"}, {PROP_Y2, "y2", "Y2"}, {PROP_X3, "x3", "X3"}, {PROP_Y3, "y3", "Y3"}};
    for (guint i = 0; i < G_N_ELEMENTS(fp); i++)
      g_object_class_install_property(oc, fp[i].id,
          g_param_spec_uint(fp[i].name, fp[i].nick, "freie Position in Promille des Verschiebewegs (nur bei Ecke 5)", 0, 1000, 0,
                            G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  }
  g_object_class_install_property(oc, PROP_SLOT3,
      g_param_spec_int("slot3", "Dritter Platz", "drittes kleines Bild im selben Durchgang (-1 = keins)", -1, NSLOTS - 1, -1,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, PROP_CORNER3,
      g_param_spec_uint("corner3", "Ecke 3", "Ecke des dritten kleinen Bildes", 0, 5, 0,
                        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  gst_element_class_set_static_metadata(ec, "IRL4YOU PiP Einblendung", "Filter/Effect/Video",
      "Schreibt das kleine Bild in das Hauptbild, ohne dieses zu lesen", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &mix_sink);
  gst_element_class_add_static_pad_template(ec, &mix_src);
  vc->transform_frame_ip = pb_mix_transform_ip;
}

static void pb_pip_mix_init(PbPipMix *self) {
  self->corner = 3;
  self->slot2 = -1;
  self->corner2 = 2;
  self->slot3 = -1;
  self->corner3 = 0;
  gst_base_transform_set_in_place(GST_BASE_TRANSFORM(self), TRUE);
}

/* ------------------------------------------------------------------ pbctl */

typedef struct {
  GstElement parent;
  gchar *file, *video_queue, *audio_queue, *pip_queue, *pip2_queue, *pip3_queue;
  GThread *thread;
  volatile gint run;
  gint64 last_mtime;
} PbCtl;
typedef struct { GstElementClass parent_class; } PbCtlClass;
G_DEFINE_TYPE(PbCtl, pb_ctl, GST_TYPE_ELEMENT)

enum { CTL_0, CTL_FILE, CTL_VQ, CTL_AQ, CTL_PQ, CTL_P2Q, CTL_P3Q };

/* Wartezeit einer benannten queue setzen. Hauptbild/Ton: Zeitlimit großzügig. Kleine Bilder: die queue hat
 * leaky=downstream und ein Zeitlimit; bei Verzögerung wird das Limit entsprechend angehoben. */
static void pb_ctl_set_queue(PbCtl *self, GstObject *par, const gchar *name, gint ms, gboolean small, gint extra) {
  if (!name || !*name || ms < 0 || ms > 3000)
    return;
  GstElement *q = gst_bin_get_by_name(GST_BIN(par), name);
  if (!q)
    return;
  /* Die queue gibt erst frei, wenn der Füllstand die Schwelle überschreitet: gemessen fehlt dabei ein Bild.
   * Beim Bild gleichen wir das mit extra (ein Bild = 33 ms) aus, beim Ton nicht (extra = 0). */
  gint th = ms ? ms + extra : 0;
  if (small)
    g_object_set(q, "max-size-time", (guint64) (th + 500) * GST_MSECOND, "max-size-buffers", (guint) (ms ? 0 : 30),
                 "min-threshold-time", (guint64) th * GST_MSECOND, NULL);
  else
    g_object_set(q, "max-size-time", (guint64) (th + 3000) * GST_MSECOND,
                 "min-threshold-time", (guint64) th * GST_MSECOND, NULL);
  gst_object_unref(q);
  GST_INFO_OBJECT(self, "%s: %d ms", name, ms);
}

static void pb_ctl_apply(PbCtl *self, gint main_ms, gint pip_ms, gint pip2_ms, gint pip3_ms) {
  GstObject *par = gst_object_get_parent(GST_OBJECT(self));
  if (!par)
    return;
  pb_ctl_set_queue(self, par, self->video_queue, main_ms, FALSE, 33);
  pb_ctl_set_queue(self, par, self->audio_queue, main_ms, FALSE, 0);
  pb_ctl_set_queue(self, par, self->pip_queue, pip_ms, TRUE, 33);
  pb_ctl_set_queue(self, par, self->pip2_queue, pip2_ms, TRUE, 33);
  pb_ctl_set_queue(self, par, self->pip3_queue, pip3_ms, TRUE, 33);
  gst_object_unref(par);
}

static gpointer pb_ctl_thread(gpointer data) {
  PbCtl *self = data;
  while (g_atomic_int_get(&self->run)) {
    struct stat st;
    if (self->file && stat(self->file, &st) == 0) {
      gint64 mt = (gint64) st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
      if (mt != self->last_mtime) {
        self->last_mtime = mt;
        int v[4] = { -1, 0, 0, 0 };         /* Hauptbild, kleine Bilder 1 bis 3 (alte Datei: weniger Werte) */
        FILE *f = fopen(self->file, "r");
        if (f) {
          if (fscanf(f, "%d %d %d %d", &v[0], &v[1], &v[2], &v[3]) < 1)
            v[0] = -1;
          fclose(f);
        }
        if (v[0] >= 0)
          pb_ctl_apply(self, v[0], v[1], v[2], v[3]);
      }
    }
    g_usleep(300000);
  }
  return NULL;
}

static GstStateChangeReturn pb_ctl_change_state(GstElement *el, GstStateChange t) {
  PbCtl *self = (PbCtl *) el;
  if (t == GST_STATE_CHANGE_PAUSED_TO_PLAYING && !self->thread) {
    self->last_mtime = 0;
    g_atomic_int_set(&self->run, 1);
    self->thread = g_thread_new("pbctl", pb_ctl_thread, self);
  }
  GstStateChangeReturn r = GST_ELEMENT_CLASS(pb_ctl_parent_class)->change_state(el, t);
  if ((t == GST_STATE_CHANGE_PLAYING_TO_PAUSED || t == GST_STATE_CHANGE_READY_TO_NULL) && self->thread) {
    g_atomic_int_set(&self->run, 0);
    g_thread_join(self->thread);
    self->thread = NULL;
  }
  return r;
}

static void pb_ctl_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbCtl *self = (PbCtl *) o;
  gchar **dst = NULL;
  switch (id) {
    case CTL_FILE: dst = &self->file; break;
    case CTL_VQ: dst = &self->video_queue; break;
    case CTL_AQ: dst = &self->audio_queue; break;
    case CTL_PQ: dst = &self->pip_queue; break;
    case CTL_P2Q: dst = &self->pip2_queue; break;
    case CTL_P3Q: dst = &self->pip3_queue; break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps); return;
  }
  g_free(*dst);
  *dst = g_value_dup_string(v);
}

static void pb_ctl_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbCtl *self = (PbCtl *) o;
  switch (id) {
    case CTL_FILE: g_value_set_string(v, self->file); break;
    case CTL_VQ: g_value_set_string(v, self->video_queue); break;
    case CTL_AQ: g_value_set_string(v, self->audio_queue); break;
    case CTL_PQ: g_value_set_string(v, self->pip_queue); break;
    case CTL_P2Q: g_value_set_string(v, self->pip2_queue); break;
    case CTL_P3Q: g_value_set_string(v, self->pip3_queue); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

static void pb_ctl_finalize(GObject *o) {
  PbCtl *self = (PbCtl *) o;
  g_free(self->file);
  g_free(self->video_queue);
  g_free(self->audio_queue);
  g_free(self->pip_queue);
  g_free(self->pip2_queue);
  g_free(self->pip3_queue);
  G_OBJECT_CLASS(pb_ctl_parent_class)->finalize(o);
}

static void pb_ctl_class_init(PbCtlClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  oc->set_property = pb_ctl_set_property;
  oc->get_property = pb_ctl_get_property;
  oc->finalize = pb_ctl_finalize;
  g_object_class_install_property(oc, CTL_FILE,
      g_param_spec_string("file", "Datei", "Datei mit der Verzögerung in Millisekunden", "/var/lib/pipbox/main-delay-ms",
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, CTL_VQ,
      g_param_spec_string("video-queue", "Bild-Warteschlange", "Name der queue für das Bild", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_AQ,
      g_param_spec_string("audio-queue", "Ton-Warteschlange", "Name der queue für den Ton", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_PQ,
      g_param_spec_string("pip-queue", "Warteschlange kleines Bild 1", "Name der queue des ersten kleinen Bildes", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_P2Q,
      g_param_spec_string("pip2-queue", "Warteschlange kleines Bild 2", "Name der queue des zweiten kleinen Bildes", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_P3Q,
      g_param_spec_string("pip3-queue", "Warteschlange kleines Bild 3", "Name der queue des dritten kleinen Bildes", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  gst_element_class_set_static_metadata(ec, "IRL4YOU PiP Steuerung", "Generic",
      "Stellt die Wartezeit zweier Warteschlangen im laufenden Betrieb um", "IRL4YOU");
  ec->change_state = pb_ctl_change_state;
}

static void pb_ctl_init(PbCtl *self) {
  self->thread = NULL;
  self->run = 0;
  self->last_mtime = 0;
}

/* ------------------------------------------------------------------ Plugin */

static gboolean plugin_init(GstPlugin *plugin) {
  GST_DEBUG_CATEGORY_INIT(pbpip_debug, "pbpip", 0, "IRL4YOU PiP");
  return gst_element_register(plugin, "pbpipsink", GST_RANK_NONE, pb_pip_sink_get_type()) &&
         gst_element_register(plugin, "pbpipmix", GST_RANK_NONE, pb_pip_mix_get_type()) &&
         gst_element_register(plugin, "pbctl", GST_RANK_NONE, pb_ctl_get_type());
}

#ifndef PACKAGE
#define PACKAGE "irl4you-pip"
#endif
GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, pbpip,
                  "IRL4YOU Bild-in-Bild: schreibt das kleine Bild in das Hauptbild",
                  plugin_init, "0.1", "MIT/X11", "irl4you-pip", "https://github.com/IRL4YOU/irl4you-pip")
