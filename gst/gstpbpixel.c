
#ifdef HAVE_CONFIG_H
#include "config.h"
#endif
#include <errno.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <math.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <glib/gstdio.h>
#include <gst/gst.h>
#include <gst/base/gstbasetransform.h>
#include <gst/video/video.h>

GST_DEBUG_CATEGORY_STATIC(pbpixel_debug);
#define GST_CAT_DEFAULT pbpixel_debug

#define PX_MAGIC_TAP 0x50425458u
#define PX_MAGIC_BOX 0x50424258u
#define PX_MAXBOX 40
#define PX_NREC 320

typedef struct { gint32 id; gint32 cells; gfloat x0, y0, x1, y1; } PxBox;
typedef struct { gint64 pts; guint32 n; guint32 pad; PxBox box[PX_MAXBOX]; } PxRec;
typedef struct { guint32 magic; volatile guint32 seq; guint32 nrec; guint32 pad; } PxBoxHdr;

typedef struct {
  guint32 magic;
  volatile guint32 seq;
  guint32 w, h;
  guint32 slot_size;
  guint32 last_slot;
  gint64 pts[2];
  guint32 slot_seq[2];
  guint32 pad[2];
} PxTapHdr;
#define TAP_DATA_OFF 128

typedef struct {
  GstBaseTransform parent;
  GstVideoInfo info;
  gboolean have_info;
  gchar *shm;
  gint interval_ms;
  gint fd;
  guint8 *map;
  gsize map_size;
  gint64 last_pts;
  guint32 count;
} PbPixelTap;
typedef struct { GstBaseTransformClass parent_class; } PbPixelTapClass;
G_DEFINE_TYPE(PbPixelTap, pb_pixel_tap, GST_TYPE_BASE_TRANSFORM)

enum { PT_0, PT_SHM, PT_INTERVAL };

static GstStaticPadTemplate pt_sink = GST_STATIC_PAD_TEMPLATE("sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));
static GstStaticPadTemplate pt_src = GST_STATIC_PAD_TEMPLATE("src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));

static void pt_unmap(PbPixelTap *s) {
  if (s->map) munmap(s->map, s->map_size);
  if (s->fd >= 0) close(s->fd);
  s->map = NULL; s->fd = -1; s->map_size = 0;
}

static gboolean pt_map(PbPixelTap *s, gint w, gint h) {
  pt_unmap(s);
  const gsize slot = (gsize) w * h * 3 / 2;
  const gsize total = TAP_DATA_OFF + 2 * slot;
  s->fd = open(s->shm, O_RDWR);
  if (s->fd < 0 && errno == ENOENT) s->fd = open(s->shm, O_RDWR | O_CREAT, 0666);
  if (s->fd < 0) {
    GST_ERROR("pbpixeltap: %s nicht beschreibbar: %s", s->shm, g_strerror(errno));
    return FALSE;
  }
  fchmod(s->fd, 0666);
  if (ftruncate(s->fd, (off_t) total) != 0) { pt_unmap(s); return FALSE; }
  s->map = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, s->fd, 0);
  if (s->map == MAP_FAILED) { s->map = NULL; pt_unmap(s); return FALSE; }
  s->map_size = total;
  PxTapHdr *hd = (PxTapHdr *) s->map;
  memset(hd, 0, sizeof *hd);
  hd->w = w; hd->h = h; hd->slot_size = (guint32) slot;
  hd->magic = PX_MAGIC_TAP;
  return TRUE;
}

static void pt_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbPixelTap *s = (PbPixelTap *) o;
  switch (id) {
    case PT_SHM: g_free(s->shm); s->shm = g_value_dup_string(v); break;
    case PT_INTERVAL: s->interval_ms = g_value_get_int(v); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}
static void pt_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbPixelTap *s = (PbPixelTap *) o;
  switch (id) {
    case PT_SHM: g_value_set_string(v, s->shm); break;
    case PT_INTERVAL: g_value_set_int(v, s->interval_ms); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}
static void pt_finalize(GObject *o) {
  PbPixelTap *s = (PbPixelTap *) o;
  pt_unmap(s);
  g_free(s->shm);
  G_OBJECT_CLASS(pb_pixel_tap_parent_class)->finalize(o);
}

static gboolean pt_set_caps(GstBaseTransform *t, GstCaps *in, GstCaps *out) {
  PbPixelTap *s = (PbPixelTap *) t;
  s->have_info = gst_video_info_from_caps(&s->info, in);
  if (s->have_info && s->shm)
    pt_map(s, GST_VIDEO_INFO_WIDTH(&s->info), GST_VIDEO_INFO_HEIGHT(&s->info));
  return TRUE;
}

static GstFlowReturn pt_transform_ip(GstBaseTransform *t, GstBuffer *buf) {
  PbPixelTap *s = (PbPixelTap *) t;
  if (!s->have_info || !s->map || !GST_BUFFER_PTS_IS_VALID(buf))
    return GST_FLOW_OK;
  const gint64 pts = (gint64) GST_BUFFER_PTS(buf);
  if (s->last_pts >= 0 && (pts < s->last_pts || pts - s->last_pts > 5 * GST_SECOND)) {
    s->last_pts = -1;
    ((PxTapHdr *) s->map)->pad[0]++;
  }
  if (s->last_pts >= 0 && pts - s->last_pts < (gint64) s->interval_ms * GST_MSECOND)
    return GST_FLOW_OK;
  GstVideoFrame fr;
  if (!gst_video_frame_map(&fr, &s->info, buf, GST_MAP_READ))
    return GST_FLOW_OK;
  PxTapHdr *hd = (PxTapHdr *) s->map;
  const guint slot = (hd->last_slot + 1) & 1;
  guint8 *dst = s->map + TAP_DATA_OFF + (gsize) slot * hd->slot_size;
  const gint w = GST_VIDEO_FRAME_WIDTH(&fr), h = GST_VIDEO_FRAME_HEIGHT(&fr);
  hd->slot_seq[slot]++;
  __sync_synchronize();
  const guint8 *py = GST_VIDEO_FRAME_PLANE_DATA(&fr, 0), *puv = GST_VIDEO_FRAME_PLANE_DATA(&fr, 1);
  const gint sy = GST_VIDEO_FRAME_PLANE_STRIDE(&fr, 0), suv = GST_VIDEO_FRAME_PLANE_STRIDE(&fr, 1);
  for (gint y = 0; y < h; y++) memcpy(dst + (gsize) y * w, py + (gsize) y * sy, (gsize) w);
  for (gint y = 0; y < h / 2; y++) memcpy(dst + (gsize) w * h + (gsize) y * w, puv + (gsize) y * suv, (gsize) w);
  gst_video_frame_unmap(&fr);
  hd->pts[slot] = pts;
  __sync_synchronize();
  hd->slot_seq[slot]++;
  hd->last_slot = slot;
  hd->seq++;
  s->last_pts = pts;
  s->count++;
  return GST_FLOW_OK;
}

static void pb_pixel_tap_class_init(PbPixelTapClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  GstBaseTransformClass *bc = GST_BASE_TRANSFORM_CLASS(klass);
  oc->set_property = pt_set_property;
  oc->get_property = pt_get_property;
  oc->finalize = pt_finalize;
  g_object_class_install_property(oc, PT_SHM, g_param_spec_string("shm", "Speicherdatei", "Pfad der Bilddatei (z. B. /dev/shm/pbpixel-tap)", "/dev/shm/pbpixel-tap", G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PT_INTERVAL, g_param_spec_int("interval-ms", "Abstand", "Abstand der gesicherten Bilder in Millisekunden", 10, 10000, 100, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  gst_element_class_set_static_metadata(ec, "IRL4YOU Verpixeln: Bild sichern", "Filter/Video", "Legt Bilder fuer die Erkennung in eine Speicherdatei", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &pt_sink);
  gst_element_class_add_static_pad_template(ec, &pt_src);
  bc->set_caps = pt_set_caps;
  bc->transform_ip = pt_transform_ip;
  bc->transform_ip_on_passthrough = TRUE;
}
static void pb_pixel_tap_init(PbPixelTap *s) {
  s->have_info = FALSE; s->shm = g_strdup("/dev/shm/pbpixel-tap"); s->interval_ms = 100; s->fd = -1; s->map = NULL; s->map_size = 0; s->last_pts = -1; s->count = 0;
  gst_base_transform_set_in_place(GST_BASE_TRANSFORM(s), TRUE);
  gst_base_transform_set_passthrough(GST_BASE_TRANSFORM(s), TRUE);
}

typedef struct {
  GstBaseTransform parent;
  GstVideoInfo info;
  gboolean have_info;
  gchar *boxes;
  gint fd;
  guint8 *map;
  gsize map_size;
  gboolean enabled;
  guint64 frames, masked, late, blanked;
  PxRec *copy;
  guint stale_ms;
  guint32 last_seq;
  gint64 last_change;
  gboolean seq_seen;
} PbPixelMask;
typedef struct { GstBaseTransformClass parent_class; } PbPixelMaskClass;
G_DEFINE_TYPE(PbPixelMask, pb_pixel_mask, GST_TYPE_BASE_TRANSFORM)

enum { PM_0, PM_BOXES, PM_ENABLED, PM_FRAMES, PM_MASKED, PM_LATE, PM_BLANKED, PM_STALE };

static GstStaticPadTemplate pm_sink = GST_STATIC_PAD_TEMPLATE("sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));
static GstStaticPadTemplate pm_src = GST_STATIC_PAD_TEMPLATE("src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));

static void pm_unmap(PbPixelMask *s) {
  if (s->map) munmap(s->map, s->map_size);
  if (s->fd >= 0) close(s->fd);
  s->map = NULL; s->fd = -1; s->map_size = 0;
}

static gboolean pm_map(PbPixelMask *s) {
  if (s->map) return TRUE;
  const gsize total = sizeof(PxBoxHdr) + (gsize) PX_NREC * sizeof(PxRec);
  s->fd = open(s->boxes, O_RDONLY);
  if (s->fd < 0) return FALSE;
  struct stat st;
  if (fstat(s->fd, &st) != 0 || (gsize) st.st_size < total) { close(s->fd); s->fd = -1; return FALSE; }
  s->map = mmap(NULL, total, PROT_READ, MAP_SHARED, s->fd, 0);
  if (s->map == MAP_FAILED) { s->map = NULL; close(s->fd); s->fd = -1; return FALSE; }
  s->map_size = total;
  return TRUE;
}

static void pm_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbPixelMask *s = (PbPixelMask *) o;
  switch (id) {
    case PM_BOXES: g_free(s->boxes); s->boxes = g_value_dup_string(v); break;
    case PM_ENABLED: s->enabled = g_value_get_boolean(v); break;
    case PM_STALE: s->stale_ms = g_value_get_uint(v); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}
static void pm_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbPixelMask *s = (PbPixelMask *) o;
  switch (id) {
    case PM_BOXES: g_value_set_string(v, s->boxes); break;
    case PM_ENABLED: g_value_set_boolean(v, s->enabled); break;
    case PM_FRAMES: g_value_set_uint64(v, s->frames); break;
    case PM_MASKED: g_value_set_uint64(v, s->masked); break;
    case PM_LATE: g_value_set_uint64(v, s->late); break;
    case PM_BLANKED: g_value_set_uint64(v, s->blanked); break;
    case PM_STALE: g_value_set_uint(v, s->stale_ms); break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}
static void pm_finalize(GObject *o) {
  PbPixelMask *s = (PbPixelMask *) o;
  pm_unmap(s);
  g_free(s->boxes);
  g_free(s->copy);
  G_OBJECT_CLASS(pb_pixel_mask_parent_class)->finalize(o);
}
static gboolean pm_set_caps(GstBaseTransform *t, GstCaps *in, GstCaps *out) {
  PbPixelMask *s = (PbPixelMask *) t;
  s->have_info = gst_video_info_from_caps(&s->info, in);
  return TRUE;
}

static void pm_mosaic(guint8 *py, gint sy, guint8 *puv, gint suv, gint fw, gint fh, gfloat fx0, gfloat fy0, gfloat fx1, gfloat fy1, gint cells) {
  gint x0 = (gint) floorf(fx0) & ~1, y0 = (gint) floorf(fy0) & ~1, x1 = ((gint) ceilf(fx1) + 1) & ~1, y1 = ((gint) ceilf(fy1) + 1) & ~1;
  if (x0 < 0) x0 = 0;
  if (y0 < 0) y0 = 0;
  if (x1 > fw) x1 = fw & ~1;
  if (y1 > fh) y1 = fh & ~1;
  if (x1 - x0 < 4 || y1 - y0 < 4) return;
  const gint bw = x1 - x0, bh = y1 - y0;

  static __thread guint8 *scr = NULL;
  static __thread gsize scrsz = 0;
  const gsize need = (gsize) bw * bh + (gsize) bw * (bh / 2);
  if (need > scrsz) {
    g_free(scr);
    scr = g_malloc(need);
    scrsz = need;
  }
  guint8 *scu = scr + (gsize) bw * bh;
  for (gint y = 0; y < bh; y++) memcpy(scr + (gsize) y * bw, py + (gsize) (y0 + y) * sy + x0, (gsize) bw);
  for (gint y = 0; y < bh / 2; y++) memcpy(scu + (gsize) y * bw, puv + (gsize) (y0 / 2 + y) * suv + x0, (gsize) bw);
  const gint shortside = bw < bh ? bw : bh;
  gint cs = (shortside + cells - 1) / cells;
  if (cs < 2) cs = 2;
  cs = (cs + 1) & ~1;
  for (gint cy = y0; cy < y1; cy += cs) {
    const gint ch = (cy + cs > y1) ? y1 - cy : cs;
    for (gint cx = x0; cx < x1; cx += cs) {
      const gint cw = (cx + cs > x1) ? x1 - cx : cs;
      guint32 sum = 0;
      for (gint y = 0; y < ch; y++) {
        const guint8 *row = scr + (gsize) (cy - y0 + y) * bw + (cx - x0);
        for (gint x = 0; x < cw; x++) sum += row[x];
      }
      const guint8 avg = (guint8) (sum / (guint32) (cw * ch));
      for (gint y = 0; y < ch; y++) memset(py + (gsize) (cy + y) * sy + cx, avg, (gsize) cw);
      guint32 su = 0, sv = 0;
      const gint cwc = cw / 2, chc = ch / 2;
      if (cwc > 0 && chc > 0) {
        for (gint y = 0; y < chc; y++) {
          const guint8 *row = scu + (gsize) ((cy - y0) / 2 + y) * bw + (cx - x0);
          for (gint x = 0; x < cwc; x++) { su += row[2 * x]; sv += row[2 * x + 1]; }
        }
        const guint8 au = (guint8) (su / (guint32) (cwc * chc)), av = (guint8) (sv / (guint32) (cwc * chc));
        for (gint y = 0; y < chc; y++) {
          guint8 *row = puv + (gsize) (cy / 2 + y) * suv + cx;
          for (gint x = 0; x < cwc; x++) { row[2 * x] = au; row[2 * x + 1] = av; }
        }
      }
    }
  }
}

static gint pm_boxes_at(const PxRec *rec, gint n, gint64 pts, PxBox *out) {
  if (n <= 0) return 0;
  gint lo = 0, hi = n - 1;
  if (pts <= rec[0].pts) lo = hi = 0;
  else if (pts >= rec[n - 1].pts) lo = hi = n - 1;
  else {
    while (hi - lo > 1) { gint m = (lo + hi) / 2; if (rec[m].pts <= pts) lo = m; else hi = m; }
  }
  const PxRec *a = &rec[lo], *b = &rec[hi];
  gint k = 0;
  const gdouble t = (hi == lo || b->pts == a->pts) ? 0.0 : (gdouble) (pts - a->pts) / (gdouble) (b->pts - a->pts);
  for (guint i = 0; i < a->n && i < PX_MAXBOX; i++) {
    PxBox r = a->box[i];
    if (hi != lo) {
      for (guint j = 0; j < b->n && j < PX_MAXBOX; j++) {
        if (b->box[j].id == r.id) {
          r.x0 = a->box[i].x0 + (gfloat) t * (b->box[j].x0 - a->box[i].x0);
          r.y0 = a->box[i].y0 + (gfloat) t * (b->box[j].y0 - a->box[i].y0);
          r.x1 = a->box[i].x1 + (gfloat) t * (b->box[j].x1 - a->box[i].x1);
          r.y1 = a->box[i].y1 + (gfloat) t * (b->box[j].y1 - a->box[i].y1);
          break;
        }
      }
    }
    out[k++] = r;
  }
  if (hi != lo && t > 0.5) {
    for (guint j = 0; j < b->n && j < PX_MAXBOX; j++) {
      gboolean found = FALSE;
      for (guint i = 0; i < a->n && i < PX_MAXBOX; i++) if (a->box[i].id == b->box[j].id) { found = TRUE; break; }
      if (!found && k < PX_MAXBOX * 2) out[k++] = b->box[j];
    }
  }
  return k;
}

static GstFlowReturn pm_transform_ip(GstBaseTransform *t, GstBuffer *buf) {
  PbPixelMask *s = (PbPixelMask *) t;
  s->frames++;
  if (!s->enabled || !s->have_info || !GST_BUFFER_PTS_IS_VALID(buf))
    return GST_FLOW_OK;
  const gboolean mapped = pm_map(s);
  const PxBoxHdr *hd = (const PxBoxHdr *) s->map;
  if (mapped && hd->magic == PX_MAGIC_BOX && hd->pad == 2)
    return GST_FLOW_OK;
  if (s->stale_ms > 0) {

    const gint64 now = g_get_monotonic_time();
    if (mapped && hd->magic == PX_MAGIC_BOX) {
      const guint32 sq = hd->seq;
      if (!s->seq_seen) { s->seq_seen = TRUE; s->last_seq = sq; }
      else if (sq != s->last_seq) { s->last_seq = sq; s->last_change = now; }
    }
    if (s->last_change == 0 || now - s->last_change > (gint64) s->stale_ms * 1000) {
      GstVideoFrame bf;
      if (gst_video_frame_map(&bf, &s->info, buf, GST_MAP_READ | GST_MAP_WRITE)) {
        guint8 *by = GST_VIDEO_FRAME_PLANE_DATA(&bf, 0), *bu = GST_VIDEO_FRAME_PLANE_DATA(&bf, 1);
        const gint bsy = GST_VIDEO_FRAME_PLANE_STRIDE(&bf, 0), bsu = GST_VIDEO_FRAME_PLANE_STRIDE(&bf, 1);
        const gint bw = GST_VIDEO_FRAME_WIDTH(&bf), bh = GST_VIDEO_FRAME_HEIGHT(&bf);
        for (gint y = 0; y < bh; y++) memset(by + (gsize) y * bsy, 16, (gsize) bw);
        for (gint y = 0; y < bh / 2; y++) memset(bu + (gsize) y * bsu, 128, (gsize) bw);
        gst_video_frame_unmap(&bf);
        s->blanked++;
      }
      return GST_FLOW_OK;
    }
  }
  if (!mapped || hd->magic != PX_MAGIC_BOX)
    return GST_FLOW_OK;
  if (!s->copy) s->copy = g_malloc((gsize) PX_NREC * sizeof(PxRec));
  guint32 nrec = 0;
  for (gint tries = 0; tries < 50; tries++) {
    const guint32 s1 = hd->seq;
    if (s1 & 1) { g_usleep(200); continue; }
    __sync_synchronize();
    nrec = hd->nrec > PX_NREC ? PX_NREC : hd->nrec;
    memcpy(s->copy, s->map + sizeof(PxBoxHdr), (gsize) nrec * sizeof(PxRec));
    __sync_synchronize();
    if (hd->seq == s1) break;
    nrec = 0;
  }
  const gint64 pts = (gint64) GST_BUFFER_PTS(buf);
  if (nrec == 0)
    return GST_FLOW_OK;
  if (pts > s->copy[nrec - 1].pts + 400 * GST_MSECOND)
    s->late++;
  PxBox boxes[PX_MAXBOX * 2];
  const gint nb = pm_boxes_at(s->copy, (gint) nrec, pts, boxes);
  if (nb <= 0)
    return GST_FLOW_OK;
  GstVideoFrame fr;
  if (!gst_video_frame_map(&fr, &s->info, buf, GST_MAP_READ | GST_MAP_WRITE))
    return GST_FLOW_OK;
  guint8 *py = GST_VIDEO_FRAME_PLANE_DATA(&fr, 0), *puv = GST_VIDEO_FRAME_PLANE_DATA(&fr, 1);
  const gint sy = GST_VIDEO_FRAME_PLANE_STRIDE(&fr, 0), suv = GST_VIDEO_FRAME_PLANE_STRIDE(&fr, 1);
  const gint fw = GST_VIDEO_FRAME_WIDTH(&fr), fh = GST_VIDEO_FRAME_HEIGHT(&fr);
  for (gint i = 0; i < nb; i++)
    pm_mosaic(py, sy, puv, suv, fw, fh, boxes[i].x0, boxes[i].y0, boxes[i].x1, boxes[i].y1, boxes[i].cells > 0 ? boxes[i].cells : 3);
  gst_video_frame_unmap(&fr);
  s->masked += (guint64) nb;
  return GST_FLOW_OK;
}

static void pb_pixel_mask_class_init(PbPixelMaskClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  GstBaseTransformClass *bc = GST_BASE_TRANSFORM_CLASS(klass);
  oc->set_property = pm_set_property;
  oc->get_property = pm_get_property;
  oc->finalize = pm_finalize;
  g_object_class_install_property(oc, PM_BOXES, g_param_spec_string("boxes", "Rechteckdatei", "Pfad der Rechteckdatei (z. B. /dev/shm/pbpixel-boxes)", "/dev/shm/pbpixel-boxes", G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PM_ENABLED, g_param_spec_boolean("enabled", "Aktiv", "Verpixeln an/aus", TRUE, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  g_object_class_install_property(oc, PM_FRAMES, g_param_spec_uint64("frames", "Bilder", "durchgelaufene Bilder", 0, G_MAXUINT64, 0, G_PARAM_READABLE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PM_MASKED, g_param_spec_uint64("masked", "Rechtecke", "verpixelte Rechtecke insgesamt", 0, G_MAXUINT64, 0, G_PARAM_READABLE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PM_BLANKED, g_param_spec_uint64("blanked", "schwarz", "Bilder, die schwarz gesetzt wurden (Dienst ohne Lebenszeichen)", 0, G_MAXUINT64, 0, G_PARAM_READABLE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PM_STALE, g_param_spec_uint("stale-ms", "Frist", "Ohne Lebenszeichen des Dienstes so lange (ms), dann wird das Bild schwarz; 0 = aus", 0, 600000, 4000, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, PM_LATE, g_param_spec_uint64("late", "zu spaet", "Bilder, zu denen die Rechtecke noch fehlten", 0, G_MAXUINT64, 0, G_PARAM_READABLE | G_PARAM_STATIC_STRINGS));
  gst_element_class_set_static_metadata(ec, "IRL4YOU Verpixeln: Mosaik", "Filter/Video", "Verpixelt Rechtecke aus einer Speicherdatei", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &pm_sink);
  gst_element_class_add_static_pad_template(ec, &pm_src);
  bc->set_caps = pm_set_caps;
  bc->transform_ip = pm_transform_ip;
  bc->transform_ip_on_passthrough = TRUE;
}
static void pb_pixel_mask_init(PbPixelMask *s) {
  s->have_info = FALSE; s->boxes = g_strdup("/dev/shm/pbpixel-boxes"); s->fd = -1; s->map = NULL; s->map_size = 0; s->enabled = TRUE; s->frames = s->masked = s->late = s->blanked = 0; s->copy = NULL; s->stale_ms = 4000; s->last_seq = 0; s->last_change = 0; s->seq_seen = FALSE;
  gst_base_transform_set_in_place(GST_BASE_TRANSFORM(s), TRUE);
  gst_base_transform_set_passthrough(GST_BASE_TRANSFORM(s), FALSE);
}

static gboolean plugin_init(GstPlugin *plugin) {
  GST_DEBUG_CATEGORY_INIT(pbpixel_debug, "pbpixel", 0, "IRL4YOU Verpixeln");
  return gst_element_register(plugin, "pbpixeltap", GST_RANK_NONE, pb_pixel_tap_get_type()) &&
         gst_element_register(plugin, "pbpixelmask", GST_RANK_NONE, pb_pixel_mask_get_type());
}

#ifndef PACKAGE
#define PACKAGE "irl4you-pip"
#endif
GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, pbpixel,
                  "IRL4YOU BOX Verpixeln im Bildweg",
                  plugin_init, "0.1", "MIT/X11", "irl4you-pip", "https://github.com/IRL4YOU/irl4you-pip")
