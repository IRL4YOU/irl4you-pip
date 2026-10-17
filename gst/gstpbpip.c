
#ifdef HAVE_CONFIG_H
#include "config.h"
#endif
#include <string.h>
#include <stdio.h>
#include <sys/stat.h>
#include <glib/gstdio.h>
#include <gst/gst.h>
#include <gst/base/gstbasesink.h>
#include <gst/video/video.h>
#include <gst/video/gstvideofilter.h>

GST_DEBUG_CATEGORY_STATIC(pbpip_debug);
#define GST_CAT_DEFAULT pbpip_debug

#define RING_CAP 12
#define PREBUFFER 3
#define NSLOTS 4
#define STALE_US (2 * G_USEC_PER_SEC)

static GMutex ring_lock;
typedef struct {
  guint8 *slot[RING_CAP];
  gsize slot_size;
  guint rd, count;
  gint w, h;
  gboolean started;
  gint64 last_push;
  guint8 *cur;
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
  if (ring->count == RING_CAP) {
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
  gst_base_sink_set_sync(GST_BASE_SINK(self), FALSE);
  gst_base_sink_set_async_enabled(GST_BASE_SINK(self), FALSE);
  self->have_info = FALSE;
}

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

  if (corner != 5u) {
    if (*x > mw - pw) *x = mw - pw;
    if (*y > mh - ph) *y = mh - ph;
    if (*x < 0) *x = 0;
    if (*y < 0) *y = 0;
  }
}

#define PB_SHIFT 10
#define PB_ONE (1 << PB_SHIFT)
#define PB_HALF (1 << (2 * PB_SHIFT - 1))
#define PB_CHUNK 128

typedef struct {
  gint op;
  gint cl, cr, ct, cb;
  gint bw;
  guint bc;
  gint bo;
  gint br;
} PbStyle;

typedef struct { gint cx, cy, cw, ch, x, y; } PbPlace;

static void pb_style_default(PbStyle *st) {
  st->op = 100;
  st->cl = st->cr = st->ct = st->cb = 0;
  st->bw = 0;
  st->bc = 0xffffffu;
  st->bo = 100;
  st->br = 0;
}

static gboolean pb_tok_is(const char *s, const char *e, const char *lit) {
  while (s < e && *lit && *s == *lit) {
    s++;
    lit++;
  }
  return s == e && *lit == 0;
}

static void pb_tok_trim(const char **s, const char **e) {
  while (*s < *e && (**s == ' ' || **s == '\t'))
    (*s)++;
  while (*e > *s && ((*e)[-1] == ' ' || (*e)[-1] == '\t'))
    (*e)--;
}

static gboolean pb_tok_num(const char *s, const char *e, gint *out) {
  gboolean neg = FALSE;
  gint v = 0;
  if (s < e && (*s == '-' || *s == '+')) {
    neg = (*s == '-');
    s++;
  }
  if (s >= e)
    return FALSE;
  for (; s < e; s++) {
    if (*s < '0' || *s > '9')
      return FALSE;
    if (v < 1000000)
      v = v * 10 + (*s - '0');
  }
  *out = neg ? -v : v;
  return TRUE;
}

static gboolean pb_tok_hex6(const char *s, const char *e, guint *out) {
  guint v = 0;
  if (e - s != 6)
    return FALSE;
  for (; s < e; s++) {
    guint d;
    if (*s >= '0' && *s <= '9') d = (guint) (*s - '0');
    else if (*s >= 'a' && *s <= 'f') d = (guint) (*s - 'a' + 10);
    else if (*s >= 'A' && *s <= 'F') d = (guint) (*s - 'A' + 10);
    else return FALSE;
    v = (v << 4) | d;
  }
  *out = v;
  return TRUE;
}

#define PB_CLAMP(v, lo, hi) MIN(MAX((v), (lo)), (hi))

static void pb_style_parse(const char *text, PbStyle *st) {
  pb_style_default(st);
  if (!text)
    return;
  const char *p = text;
  while (*p) {
    const char *ts = p;
    while (*p && *p != ',')
      p++;
    const char *te = p;
    if (*p == ',')
      p++;
    const char *eq = ts;
    while (eq < te && *eq != '=')
      eq++;
    if (eq == te)
      continue;
    const char *ks = ts, *ke = eq, *vs = eq + 1, *ve = te;
    pb_tok_trim(&ks, &ke);
    pb_tok_trim(&vs, &ve);
    gint n = 0;
    guint rgb = 0;
    if (pb_tok_is(ks, ke, "bc")) {
      if (pb_tok_hex6(vs, ve, &rgb))
        st->bc = rgb;
    } else if (pb_tok_num(vs, ve, &n)) {
      if (pb_tok_is(ks, ke, "op")) st->op = PB_CLAMP(n, 0, 100);
      else if (pb_tok_is(ks, ke, "cl")) st->cl = PB_CLAMP(n, 0, 1900);
      else if (pb_tok_is(ks, ke, "cr")) st->cr = PB_CLAMP(n, 0, 1900);
      else if (pb_tok_is(ks, ke, "ct")) st->ct = PB_CLAMP(n, 0, 1900);
      else if (pb_tok_is(ks, ke, "cb")) st->cb = PB_CLAMP(n, 0, 1900);
      else if (pb_tok_is(ks, ke, "bw")) st->bw = PB_CLAMP(n, 0, 64);
      else if (pb_tok_is(ks, ke, "bo")) st->bo = PB_CLAMP(n, 0, 100);
      else if (pb_tok_is(ks, ke, "br")) st->br = PB_CLAMP(n, 0, 200);
    }
  }
}

static gboolean pb_style_crop(const PbStyle *st, gint sw, gint sh, gint *cx, gint *cy, gint *cw, gint *ch) {
  *cx = 0;
  *cy = 0;
  *cw = sw;
  *ch = sh;
  if (st->cl <= 0 && st->cr <= 0 && st->ct <= 0 && st->cb <= 0)
    return FALSE;
  const gint l = ((st->cl * sw + 960) / 1920) & ~1, r = ((st->cr * sw + 960) / 1920) & ~1;
  const gint t = ((st->ct * sh + 540) / 1080) & ~1, b = ((st->cb * sh + 540) / 1080) & ~1;
  if (l == 0 && r == 0 && t == 0 && b == 0)
    return FALSE;
  const gint w = (sw - l - r) & ~1, h = (sh - t - b) & ~1;
  if (w < 16 || h < 16)
    return FALSE;
  *cx = l;
  *cy = t;
  *cw = w;
  *ch = h;
  return TRUE;
}

static gboolean pb_place(const PbStyle *st, gint mw, gint mh, gint pw, gint ph, guint corner, gint fx, gint fy, PbPlace *pl) {
  if (st->op <= 0)
    return FALSE;
  pb_style_crop(st, pw, ph, &pl->cx, &pl->cy, &pl->cw, &pl->ch);
  if (pl->cw > mw || pl->ch > mh)
    return FALSE;
  const gint margin = (mw / 60) & ~1;
  pb_pos(corner, mw, mh, pl->cw, pl->ch, margin, fx, fy, &pl->x, &pl->y);
  pl->x &= ~1;
  pl->y &= ~1;
  return pl->x >= 0 && pl->y >= 0 && pl->x + pl->cw <= mw && pl->y + pl->ch <= mh;
}

static void pb_rgb_to_nv12(guint rgb, gint *y, gint *u, gint *v) {
  const gint r = (gint) ((rgb >> 16) & 0xffu), g = (gint) ((rgb >> 8) & 0xffu), b = (gint) (rgb & 0xffu);
  const gint den = 255 * 10000;
  *y = 16 + (219 * (2126 * r + 7152 * g + 722 * b) + den / 2) / den;
  *u = (128 * den + 224 * (-1146 * r - 3854 * g + 5000 * b) + den / 2) / den;
  *v = (128 * den + 224 * (5000 * r - 4542 * g - 458 * b) + den / 2) / den;
  *y = PB_CLAMP(*y, 16, 235);
  *u = PB_CLAMP(*u, 16, 240);
  *v = PB_CLAMP(*v, 16, 240);
}

typedef struct {
  gint cw, ch;
  gint bw;
  gint ro, ri;
  gint op, bo;
} PbShape;

static void pb_shape_init(PbShape *s, const PbStyle *st, gint mw, gint cw, gint ch) {
  const gint lim = MIN(cw, ch) / 2;
  s->cw = cw;
  s->ch = ch;
  s->bw = s->ro = s->ri = 0;
  if (st->bw > 0)
    s->bw = MIN(MAX((st->bw * mw + 960) / 1920, 1), lim);
  if (st->br > 0) {
    s->ro = MIN((st->br * mw + 960) / 1920, lim);
    s->ri = MAX(s->ro - s->bw, 0);
  }
  s->op = (PB_CLAMP(st->op, 0, 100) * PB_ONE + 50) / 100;
  s->bo = (PB_CLAMP(st->bo, 0, 100) * PB_ONE + 50) / 100;
}

static gint64 pb_isqrt(gint64 v) {
  gint64 r = 0, bit = (gint64) 1 << 62;
  while (bit > v)
    bit >>= 2;
  while (bit != 0) {
    if (v >= r + bit) {
      v -= r + bit;
      r = (r >> 1) + bit;
    } else {
      r >>= 1;
    }
    bit >>= 2;
  }
  return r;
}

static gint pb_corner_cov(gint fx, gint fy, gint r) {
  const gint64 ux = 2 * (gint64) (r - fx) - 1, uy = 2 * (gint64) (r - fy) - 1;
  const gint64 d2 = ux * ux + uy * uy;
  const gint64 in = 2 * (gint64) r - 1, out = 2 * (gint64) r + 1;
  if (d2 <= in * in)
    return PB_ONE;
  if (d2 >= out * out)
    return 0;
  const gint64 s = pb_isqrt(d2 << (2 * PB_SHIFT));
  const gint64 c = (out * PB_ONE - s + 1) / 2;
  return (gint) PB_CLAMP(c, 0, PB_ONE);
}

static void pb_shape_px(const PbShape *s, gint x, gint y, gint *wb, gint *a) {
  const gint fx = MIN(x, s->cw - 1 - x), fy = MIN(y, s->ch - 1 - y);
  gint ao = PB_ONE, ai = PB_ONE;
  if (s->ro > 0 && fx < s->ro && fy < s->ro)
    ao = pb_corner_cov(fx, fy, s->ro);
  if (s->bw > 0) {
    const gint ix = fx - s->bw, iy = fy - s->bw;
    if (ix < 0 || iy < 0)
      ai = 0;
    else if (s->ri > 0 && ix < s->ri && iy < s->ri)
      ai = pb_corner_cov(ix, iy, s->ri);
  }
  *a = (s->op * ao + PB_ONE / 2) >> PB_SHIFT;
  *wb = (s->bo * (PB_ONE - ai) + PB_ONE / 2) >> PB_SHIFT;
}

static gint pb_row_zone(const PbShape *s, gint y, gint *wb, gint *a) {
  const gint fy = MIN(y, s->ch - 1 - y);
  gint zone = 0;
  *a = s->op;
  *wb = 0;
  if (s->bw <= 0) {
    if (fy < s->ro)
      zone = s->ro;
    return zone;
  }
  if (fy < s->bw) {
    *wb = s->bo;
    if (fy < s->ro)
      zone = s->ro;
  } else {
    zone = s->bw;
    if (fy < s->ro)
      zone = MAX(zone, s->ro);
    if (s->ri > 0 && fy - s->bw < s->ri)
      zone = MAX(zone, s->bw + s->ri);
  }
  return zone;
}

static gint pb_mix(gint d, gint p, gint b, gint wb, gint a) {
  return (d * (PB_ONE - a) * PB_ONE + (p * (PB_ONE - wb) + b * wb) * a + PB_HALF) >> (2 * PB_SHIFT);
}

static void pb_y_vec(guint8 *dst, const guint8 *src, gint n, gint b, const gint *wbv, const gint *av, gint wc, gint ac) {
  gint i = 0;
  while (i < n) {
    while (i < n && (av ? av[i] : ac) <= 0)
      i++;
    gint j = i;
    gboolean rd = FALSE;
    while (j < n && (av ? av[j] : ac) > 0) {
      if ((av ? av[j] : ac) < PB_ONE)
        rd = TRUE;
      j++;
    }
    if (j > i) {
      guint8 buf[PB_CHUNK];
      if (rd)
        memcpy(buf, dst + i, (gsize) (j - i));
      for (gint k = i; k < j; k++) {
        const gint a = av ? av[k] : ac, wb = wbv ? wbv[k] : wc;
        buf[k - i] = (guint8) pb_mix(rd ? buf[k - i] : 0, src[k], b, wb, a);
      }
      memcpy(dst + i, buf, (gsize) (j - i));
    }
    i = j;
  }
}

static void pb_uv_vec(guint8 *dst, const guint8 *src, gint n, gint bu, gint bv, const gint *wbv, const gint *av, gint wc, gint ac) {
  gint i = 0;
  while (i < n) {
    while (i < n && (av ? av[i] : ac) <= 0)
      i++;
    gint j = i;
    gboolean rd = FALSE;
    while (j < n && (av ? av[j] : ac) > 0) {
      if ((av ? av[j] : ac) < PB_ONE)
        rd = TRUE;
      j++;
    }
    if (j > i) {
      guint8 buf[2 * PB_CHUNK];
      if (rd)
        memcpy(buf, dst + 2 * i, (gsize) (j - i) * 2);
      for (gint k = i; k < j; k++) {
        const gint a = av ? av[k] : ac, wb = wbv ? wbv[k] : wc, o = 2 * (k - i);
        buf[o] = (guint8) pb_mix(rd ? buf[o] : 0, src[2 * k], bu, wb, a);
        buf[o + 1] = (guint8) pb_mix(rd ? buf[o + 1] : 0, src[2 * k + 1], bv, wb, a);
      }
      memcpy(dst + 2 * i, buf, (gsize) (j - i) * 2);
    }
    i = j;
  }
}

static void pb_y_run(guint8 *dst, const guint8 *src, gint n, gint b, gint wb, gint a) {
  if (n <= 0 || a <= 0)
    return;
  if (a >= PB_ONE && wb <= 0) {
    memcpy(dst, src, (gsize) n);
    return;
  }
  if (a >= PB_ONE && wb >= PB_ONE) {
    memset(dst, b, (gsize) n);
    return;
  }
  for (gint i = 0; i < n; i += PB_CHUNK)
    pb_y_vec(dst + i, src + i, MIN(PB_CHUNK, n - i), b, NULL, NULL, wb, a);
}

static void pb_uv_run(guint8 *dst, const guint8 *src, gint n, gint bu, gint bv, gint wb, gint a) {
  if (n <= 0 || a <= 0)
    return;
  if (a >= PB_ONE && wb <= 0) {
    memcpy(dst, src, (gsize) n * 2);
    return;
  }
  for (gint i = 0; i < n; i += PB_CHUNK)
    pb_uv_vec(dst + 2 * i, src + 2 * i, MIN(PB_CHUNK, n - i), bu, bv, NULL, NULL, wb, a);
}

static void pb_y_edge(const PbShape *s, gint y, gint x0, gint x1, guint8 *d, const guint8 *p, gint b) {
  gint wbv[PB_CHUNK], av[PB_CHUNK];
  for (gint x = x0; x < x1; x += PB_CHUNK) {
    const gint n = MIN(PB_CHUNK, x1 - x);
    for (gint i = 0; i < n; i++)
      pb_shape_px(s, x + i, y, &wbv[i], &av[i]);
    pb_y_vec(d + x, p + x, n, b, wbv, av, 0, 0);
  }
}

static void pb_uv_edge(const PbShape *s, gint y, gint k0, gint k1, guint8 *d, const guint8 *p, gint bu, gint bv) {
  gint wbv[PB_CHUNK], av[PB_CHUNK];
  for (gint k = k0; k < k1; k += PB_CHUNK) {
    const gint n = MIN(PB_CHUNK, k1 - k);
    for (gint i = 0; i < n; i++) {
      gint w4 = 0, a4 = 0;
      for (gint q = 0; q < 4; q++) {
        gint w, a;
        pb_shape_px(s, 2 * (k + i) + (q & 1), y + (q >> 1), &w, &a);
        w4 += w;
        a4 += a;
      }
      wbv[i] = (w4 + 2) >> 2;
      av[i] = (a4 + 2) >> 2;
    }
    pb_uv_vec(d + 2 * k, p + 2 * k, n, bu, bv, wbv, av, 0, 0);
  }
}

static void pb_draw_picture(guint8 *dy, gint ystride, guint8 *duv, gint uvstride, gint mw,
                            const guint8 *src, gint pw, gint ph, const PbPlace *pl, const PbStyle *st) {
  if (st->op <= 0)
    return;
  const gint cx = pl->cx, cy = pl->cy, x = pl->x, y = pl->y;
  const guint8 *suv = src + (gsize) pw * ph;
  PbShape s;
  pb_shape_init(&s, st, mw, pl->cw & ~1, pl->ch & ~1);
  if (st->op >= 100 && s.bw <= 0 && s.ro <= 0) {
    for (gint r = 0; r < pl->ch; r++)
      memcpy(dy + (gsize) (y + r) * ystride + x, src + (gsize) (cy + r) * pw + cx, (gsize) pl->cw);
    for (gint r = 0; r < pl->ch / 2; r++)
      memcpy(duv + (gsize) (y / 2 + r) * uvstride + x, suv + (gsize) (cy / 2 + r) * pw + cx, (gsize) pl->cw);
    return;
  }
  gint yb, ub, vb;
  pb_rgb_to_nv12(st->bc, &yb, &ub, &vb);
  for (gint r = 0; r < s.ch; r++) {
    gint wb, a;
    const gint zone = pb_row_zone(&s, r, &wb, &a);
    guint8 *d = dy + (gsize) (y + r) * ystride + x;
    const guint8 *p = src + (gsize) (cy + r) * pw + cx;
    const gint le = MIN(zone, s.cw), rs = MAX(s.cw - zone, le);
    pb_y_edge(&s, r, 0, le, d, p, yb);
    pb_y_run(d + le, p + le, rs - le, yb, wb, a);
    pb_y_edge(&s, r, rs, s.cw, d, p, yb);
  }
  const gint np = s.cw / 2;
  for (gint m = 0; m < s.ch / 2; m++) {
    gint wb0, a0, wb1, a1;
    const gint z0 = pb_row_zone(&s, 2 * m, &wb0, &a0), z1 = pb_row_zone(&s, 2 * m + 1, &wb1, &a1);
    const gint zone = MAX(z0, z1);
    guint8 *d = duv + (gsize) (y / 2 + m) * uvstride + x;
    const guint8 *p = suv + (gsize) (cy / 2 + m) * pw + cx;
    const gint kz = (zone + 1) / 2, le = MIN(kz, np), rs = MAX(np - kz, le);
    pb_uv_edge(&s, 2 * m, 0, le, d, p, ub, vb);
    pb_uv_run(d + 2 * le, p + 2 * le, rs - le, ub, vb, (wb0 + wb1 + 1) >> 1, (a0 + a1 + 1) >> 1);
    pb_uv_edge(&s, 2 * m, rs, np, d, p, ub, vb);
  }
}

typedef struct { GstVideoFilter parent; guint corner; guint slot; gint slot2; guint corner2; gint slot3; guint corner3; guint fx, fy, fx2, fy2, fx3, fy3; gint follow_tag; PbStyle style[3]; gchar *style_txt[3]; } PbPipMix;
typedef struct { GstVideoFilterClass parent_class; } PbPipMixClass;
G_DEFINE_TYPE(PbPipMix, pb_pip_mix, GST_TYPE_VIDEO_FILTER)

enum { PROP_0, PROP_CORNER, PROP_WIDTH_PCT, PROP_SLOT, PROP_SLOT2, PROP_CORNER2, PROP_SLOT3, PROP_CORNER3, PROP_X, PROP_Y, PROP_X2, PROP_Y2, PROP_X3, PROP_Y3, PROP_FOLLOW,
       PROP_STYLE1, PROP_STYLE2, PROP_STYLE3 };

static GstStaticPadTemplate mix_sink = GST_STATIC_PAD_TEMPLATE(
    "sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));
static GstStaticPadTemplate mix_src = GST_STATIC_PAD_TEMPLATE(
    "src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));

static void pb_mix_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbPipMix *self = (PbPipMix *) o;
  switch (id) {
    case PROP_CORNER: g_atomic_int_set(&self->corner, MIN(g_value_get_uint(v), 5u)); break;
    case PROP_WIDTH_PCT: break;
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
    case PROP_FOLLOW: g_atomic_int_set(&self->follow_tag, g_value_get_boolean(v) ? 1 : 0); break;
    case PROP_STYLE1: case PROP_STYLE2: case PROP_STYLE3: {

      const guint i = id - PROP_STYLE1;
      g_mutex_lock(&ring_lock);
      g_free(self->style_txt[i]);
      self->style_txt[i] = g_value_dup_string(v);
      pb_style_parse(self->style_txt[i], &self->style[i]);
      g_mutex_unlock(&ring_lock);
      break;
    }
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
    case PROP_FOLLOW: g_value_set_boolean(v, g_atomic_int_get(&self->follow_tag)); break;
    case PROP_STYLE1: case PROP_STYLE2: case PROP_STYLE3: {
      const guint i = id - PROP_STYLE1;
      g_mutex_lock(&ring_lock);
      g_value_set_string(v, self->style_txt[i] ? self->style_txt[i] : "");
      g_mutex_unlock(&ring_lock);
      break;
    }
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

static void pb_mix_finalize(GObject *o) {
  PbPipMix *self = (PbPipMix *) o;
  for (guint i = 0; i < G_N_ELEMENTS(self->style_txt); i++)
    g_free(self->style_txt[i]);
  G_OBJECT_CLASS(pb_pip_mix_parent_class)->finalize(o);
}

static void pb_mix_draw(GstVideoFrame *frame, guint slot, guint corner, gint fx, gint fy, const PbStyle *st) {
  Ring *ring = &rings[slot];
  if (ring->w <= 0 || ring->slot_size == 0)
    return;
  if (!ring->started && ring->count >= PREBUFFER)
    ring->started = TRUE;
  while (ring->count > PREBUFFER + 3) {
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

  PbPlace pl = { 0, 0, 0, 0, 0, 0 };
  if (draw && pb_place(st, mw, mh, pw, ph, corner, fx, fy, &pl)) {
    guint8 *dy = GST_VIDEO_FRAME_PLANE_DATA(frame, 0), *duv = GST_VIDEO_FRAME_PLANE_DATA(frame, 1);
    const gint sy = GST_VIDEO_FRAME_PLANE_STRIDE(frame, 0), suv = GST_VIDEO_FRAME_PLANE_STRIDE(frame, 1);
    pb_draw_picture(dy, sy, duv, suv, mw, ring->cur, pw, ph, &pl, st);
  }
}

static void pb_mix_trim(guint slot) {
  Ring *ring = &rings[slot];
  while (ring->count > PREBUFFER) {
    ring->rd = (ring->rd + 1) % RING_CAP;
    ring->count--;
    ring->dropped++;
  }
}

static GstFlowReturn pb_mix_transform_ip(GstVideoFilter *filter, GstVideoFrame *frame) {
  PbPipMix *self = (PbPipMix *) filter;

  const guint64 tag = GST_BUFFER_OFFSET(frame->buffer);
  const gboolean follow = g_atomic_int_get(&self->follow_tag) && (tag >> 60) == 9;
  g_mutex_lock(&ring_lock);
  if (follow) {
    const guint cn[3] = { g_atomic_int_get(&self->corner), g_atomic_int_get(&self->corner2), g_atomic_int_get(&self->corner3) };
    const gint fx[3] = { g_atomic_int_get(&self->fx), g_atomic_int_get(&self->fx2), g_atomic_int_get(&self->fx3) };
    const gint fy[3] = { g_atomic_int_get(&self->fy), g_atomic_int_get(&self->fy2), g_atomic_int_get(&self->fy3) };
    guint shown = 0;
    for (guint p = 0; p < 3; p++) {
      const guint cam = (guint) ((tag >> (4 * (p + 1))) & 0xF);
      if (cam > 3)
        continue;
      const guint ring = (cam + 3) % NSLOTS;
      pb_mix_draw(frame, ring, cn[p], fx[p], fy[p], &self->style[p]);
      shown |= 1u << ring;
    }
    for (guint r = 0; r < NSLOTS; r++)
      if (!(shown & (1u << r)))
        pb_mix_trim(r);
  } else {
    pb_mix_draw(frame, self->slot, g_atomic_int_get(&self->corner), g_atomic_int_get(&self->fx), g_atomic_int_get(&self->fy), &self->style[0]);
    if (self->slot2 >= 0)
      pb_mix_draw(frame, (guint) self->slot2, g_atomic_int_get(&self->corner2), g_atomic_int_get(&self->fx2), g_atomic_int_get(&self->fy2), &self->style[1]);
    if (self->slot3 >= 0)
      pb_mix_draw(frame, (guint) self->slot3, g_atomic_int_get(&self->corner3), g_atomic_int_get(&self->fx3), g_atomic_int_get(&self->fy3), &self->style[2]);
  }
  g_mutex_unlock(&ring_lock);
  return GST_FLOW_OK;
}

static void pb_pip_mix_class_init(PbPipMixClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  GstVideoFilterClass *vc = GST_VIDEO_FILTER_CLASS(klass);
  oc->set_property = pb_mix_set_property;
  oc->get_property = pb_mix_get_property;
  oc->finalize = pb_mix_finalize;
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
  g_object_class_install_property(oc, PROP_FOLLOW,
      g_param_spec_boolean("follow-tag", "Zustand aus dem Bild", "kleine Bilder nach dem Zustand zeigen, den pbpipsel ins Bild schreibt (Tausch ohne Unterbrechung)",
                           FALSE, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  {

    static const struct { guint id; const gchar *name; const gchar *nick; } sp[] = {
      {PROP_STYLE1, "style1", "Stil 1"}, {PROP_STYLE2, "style2", "Stil 2"}, {PROP_STYLE3, "style3", "Stil 3"}};
    for (guint i = 0; i < G_N_ELEMENTS(sp); i++)
      g_object_class_install_property(oc, sp[i].id,
          g_param_spec_string(sp[i].name, sp[i].nick,
                              "Gestaltung des kleinen Bildes, kommagetrennt: op (Deckkraft %), cl/cr/ct/cb (Beschnitt, Bezug 1920x1080), bw (Rahmenbreite), bc (Rahmenfarbe rrggbb), bo (Rahmendeckkraft %), br (Eckenradius); leer = unverändert",
                              "", G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  }
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
  for (guint i = 0; i < G_N_ELEMENTS(self->style); i++)
    pb_style_default(&self->style[i]);
  gst_base_transform_set_in_place(GST_BASE_TRANSFORM(self), TRUE);
}

#define RD_PIPS 3
#define RD_HIST 6
#define RD_MAXR 200
#define RD_STALE_NS G_GUINT64_CONSTANT(1000000000)

typedef struct {
  gboolean on;
  gint x, y, w, h;
  PbStyle st;
} RdPip;

typedef struct {
  gboolean ok;
  gint x, y, w, h, r;
  guint8 ys[4][RD_MAXR * RD_MAXR];
  guint8 uvs[4][(RD_MAXR / 2) * RD_MAXR];
} RdCopy;

typedef struct {
  guint64 rt;
  guint64 seq;
  gboolean valid;
  RdCopy c[RD_PIPS];
} RdEntry;

static GMutex rd_lock;
static RdPip rd_pip[RD_PIPS];
static RdEntry rd_hist[RD_HIST];
static gint rd_wr = 0;
static guint64 rd_seq = 0;
static gint rd_active = 0;

static gint rd_radius(const RdPip *p) {
  PbShape s;
  pb_shape_init(&s, &p->st, 1920, p->w, p->h);
  return (s.ro + 1) & ~1;
}

typedef struct { GstBaseTransform parent; GstVideoInfo info; gboolean have_info, active; } PbTap;
typedef struct { GstBaseTransformClass parent_class; } PbTapClass;
G_DEFINE_TYPE(PbTap, pb_tap, GST_TYPE_BASE_TRANSFORM)

enum { PROP_TAP0, PROP_TAP_ACTIVE };

static GstStaticPadTemplate tap_sink = GST_STATIC_PAD_TEMPLATE("sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));
static GstStaticPadTemplate tap_src = GST_STATIC_PAD_TEMPLATE("src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));

static void pb_tap_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbTap *self = (PbTap *) o;
  if (id != PROP_TAP_ACTIVE) {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
    return;
  }
  const gboolean now = g_value_get_boolean(v);
  g_mutex_lock(&rd_lock);
  if (now && !self->active)
    rd_active++;
  else if (!now && self->active)
    rd_active--;
  self->active = now;
  g_mutex_unlock(&rd_lock);
}

static void pb_tap_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbTap *self = (PbTap *) o;
  if (id == PROP_TAP_ACTIVE)
    g_value_set_boolean(v, self->active);
  else
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
}

static void pb_tap_finalize(GObject *o) {
  PbTap *self = (PbTap *) o;
  if (self->active) {
    g_mutex_lock(&rd_lock);
    rd_active--;
    self->active = FALSE;
    g_mutex_unlock(&rd_lock);
  }
  G_OBJECT_CLASS(pb_tap_parent_class)->finalize(o);
}

static gboolean pb_tap_set_caps(GstBaseTransform *t, GstCaps *in, GstCaps *out) {
  PbTap *self = (PbTap *) t;
  self->have_info = gst_video_info_from_caps(&self->info, in);
  return TRUE;
}

static GstFlowReturn pb_tap_transform_ip(GstBaseTransform *t, GstBuffer *buf) {
  PbTap *self = (PbTap *) t;
  if (!self->active || !self->have_info)
    return GST_FLOW_OK;
  RdPip pips[RD_PIPS];
  gint slot = -1;
  g_mutex_lock(&rd_lock);
  memcpy(pips, rd_pip, sizeof pips);
  slot = rd_wr;
  rd_wr = (rd_wr + 1) % RD_HIST;
  rd_hist[slot].valid = FALSE;
  g_mutex_unlock(&rd_lock);
  GstVideoFrame fr;
  if (!gst_video_frame_map(&fr, &self->info, buf, GST_MAP_READ))
    return GST_FLOW_OK;
  const gint fw = GST_VIDEO_FRAME_WIDTH(&fr), fh = GST_VIDEO_FRAME_HEIGHT(&fr);
  const guint8 *py = GST_VIDEO_FRAME_PLANE_DATA(&fr, 0), *puv = GST_VIDEO_FRAME_PLANE_DATA(&fr, 1);
  const gint sy = GST_VIDEO_FRAME_PLANE_STRIDE(&fr, 0), suv = GST_VIDEO_FRAME_PLANE_STRIDE(&fr, 1);
  RdEntry *e = &rd_hist[slot];
  for (gint i = 0; i < RD_PIPS; i++) {
    RdCopy *c = &e->c[i];
    const RdPip *p = &pips[i];
    c->ok = FALSE;
    if (!p->on || p->w < 16 || p->h < 16 || p->x < 0 || p->y < 0 || p->x + p->w > fw || p->y + p->h > fh)
      continue;
    const gint r = rd_radius(p);
    if (r <= 0 || r > RD_MAXR || r > p->w || r > p->h)
      continue;
    for (gint q = 0; q < 4; q++) {
      const gint x0 = p->x + ((q & 1) ? p->w - r : 0), y0 = p->y + ((q & 2) ? p->h - r : 0);
      for (gint k = 0; k < r; k++)
        memcpy(c->ys[q] + (gsize) k * r, py + (gsize) (y0 + k) * sy + x0, (gsize) r);
      for (gint k = 0; k < r / 2; k++)
        memcpy(c->uvs[q] + (gsize) k * r, puv + (gsize) (y0 / 2 + k) * suv + x0, (gsize) r);
    }
    c->x = p->x; c->y = p->y; c->w = p->w; c->h = p->h; c->r = r;
    c->ok = TRUE;
  }
  gst_video_frame_unmap(&fr);
  g_mutex_lock(&rd_lock);
  e->rt = GST_BUFFER_PTS_IS_VALID(buf) ? gst_segment_to_running_time(&t->segment, GST_FORMAT_TIME, GST_BUFFER_PTS(buf)) : GST_CLOCK_TIME_NONE;
  e->seq = ++rd_seq;
  e->valid = TRUE;
  g_mutex_unlock(&rd_lock);
  return GST_FLOW_OK;
}

static void pb_tap_class_init(PbTapClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  GstBaseTransformClass *bc = GST_BASE_TRANSFORM_CLASS(klass);
  oc->set_property = pb_tap_set_property;
  oc->get_property = pb_tap_get_property;
  oc->finalize = pb_tap_finalize;
  g_object_class_install_property(oc, PROP_TAP_ACTIVE,
      g_param_spec_boolean("active", "Aktiv", "dieser Zweig liefert gerade das Hauptbild: Ecken der gerundeten kleinen Bilder sichern",
                           FALSE, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  gst_element_class_set_static_metadata(ec, "IRL4YOU Ecken sichern", "Filter/Video", "Sichert die Ecken unter gerundeten kleinen Bildern", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &tap_sink);
  gst_element_class_add_static_pad_template(ec, &tap_src);
  bc->set_caps = pb_tap_set_caps;
  bc->transform_ip = pb_tap_transform_ip;
  bc->transform_ip_on_passthrough = TRUE;
}

static void pb_tap_init(PbTap *self) {
  self->have_info = FALSE;
  self->active = FALSE;
  gst_base_transform_set_in_place(GST_BASE_TRANSFORM(self), TRUE);
  gst_base_transform_set_passthrough(GST_BASE_TRANSFORM(self), TRUE);
}

typedef struct { GstVideoFilter parent; } PbRound;
typedef struct { GstVideoFilterClass parent_class; } PbRoundClass;
G_DEFINE_TYPE(PbRound, pb_round, GST_TYPE_VIDEO_FILTER)

enum { PROP_R0, PROP_RPIP1, PROP_RPIP2, PROP_RPIP3 };

static GstStaticPadTemplate rnd_sink = GST_STATIC_PAD_TEMPLATE("sink", GST_PAD_SINK, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));
static GstStaticPadTemplate rnd_src = GST_STATIC_PAD_TEMPLATE("src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS("video/x-raw, format=(string)NV12"));

static void pb_round_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  if (id < PROP_RPIP1 || id > PROP_RPIP3) {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
    return;
  }
  RdPip np;
  memset(&np, 0, sizeof np);
  pb_style_default(&np.st);
  const gchar *txt = g_value_get_string(v);
  gint x, y, w, h, bw, bo, br, op;
  guint bc;
  if (txt && sscanf(txt, "%d,%d,%d,%d,%d,%x,%d,%d,%d", &x, &y, &w, &h, &bw, &bc, &bo, &br, &op) == 9 && w >= 16 && h >= 16) {
    np.on = TRUE;
    np.x = x & ~1; np.y = y & ~1; np.w = w & ~1; np.h = h & ~1;
    np.st.bw = PB_CLAMP(bw, 0, 64);
    np.st.bc = bc & 0xffffffu;
    np.st.bo = PB_CLAMP(bo, 0, 100);
    np.st.br = PB_CLAMP(br, 0, 200);
    np.st.op = PB_CLAMP(op, 0, 100);
  }
  g_mutex_lock(&rd_lock);
  rd_pip[id - PROP_RPIP1] = np;
  g_mutex_unlock(&rd_lock);
}

static void pb_round_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  if (id < PROP_RPIP1 || id > PROP_RPIP3) {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
    return;
  }
  gchar buf[128] = "off";
  g_mutex_lock(&rd_lock);
  const RdPip *p = &rd_pip[id - PROP_RPIP1];
  if (p->on)
    g_snprintf(buf, sizeof buf, "%d,%d,%d,%d,%d,%06x,%d,%d,%d", p->x, p->y, p->w, p->h, p->st.bw, p->st.bc, p->st.bo, p->st.br, p->st.op);
  g_mutex_unlock(&rd_lock);
  g_value_set_string(v, buf);
}

static gboolean rd_main_px(const RdCopy *c, gint lx, gint ly, gint *Y, gint *U, gint *V) {
  const gint r = c->r, q = (ly >= c->h / 2 ? 2 : 0) + (lx >= c->w / 2 ? 1 : 0);
  const gint x0 = (q & 1) ? c->w - r : 0, y0 = (q & 2) ? c->h - r : 0;
  const gint cx = lx - x0, cy = ly - y0;
  if (cx < 0 || cy < 0 || cx >= r || cy >= r)
    return FALSE;
  *Y = c->ys[q][cy * r + cx];
  const gint ux = cx & ~1;
  *U = c->uvs[q][(cy / 2) * r + ux];
  *V = c->uvs[q][(cy / 2) * r + ux + 1];
  return TRUE;
}

static const RdCopy *rd_find(gint pip, const RdPip *p, guint64 rt) {
  const RdCopy *best = NULL;
  guint64 bestd = RD_STALE_NS, bestseq = 0;
  for (gint i = 0; i < RD_HIST; i++) {
    const RdEntry *e = &rd_hist[i];
    const RdCopy *c = &e->c[pip];
    if (!e->valid || !c->ok || c->x != p->x || c->y != p->y || c->w != p->w || c->h != p->h)
      continue;
    if (rt == GST_CLOCK_TIME_NONE || e->rt == GST_CLOCK_TIME_NONE) {
      if (e->seq > bestseq && bestd == RD_STALE_NS) {
        bestseq = e->seq;
        best = c;
      }
      continue;
    }
    const guint64 d = e->rt > rt ? e->rt - rt : rt - e->rt;
    if (d < bestd) {
      bestd = d;
      best = c;
      bestseq = G_MAXUINT64;
    }
  }
  return best;
}

#define PB_RMIX(a, b, w) (((a) * (PB_ONE - (w)) + (b) * (w) + PB_ONE / 2) >> PB_SHIFT)

static void pb_round_pip(GstVideoFrame *f, gint idx, const RdPip *p, guint64 rt, gboolean black_ok) {
  const gint fw = GST_VIDEO_FRAME_WIDTH(f), fh = GST_VIDEO_FRAME_HEIGHT(f);
  if (p->x < 0 || p->y < 0 || p->x + p->w > fw || p->y + p->h > fh || p->st.op <= 0)
    return;
  PbStyle st = p->st;
  const gint opf = (st.op * PB_ONE + 50) / 100;
  st.op = 100;
  PbShape s;
  pb_shape_init(&s, &st, 1920, p->w, p->h);
  if (s.ro <= 0 && s.bw <= 0)
    return;
  const RdCopy *mc = rd_find(idx, p, rt);
  gint yb, ub, vb;
  pb_rgb_to_nv12(st.bc, &yb, &ub, &vb);
  guint8 *dy = GST_VIDEO_FRAME_PLANE_DATA(f, 0), *duv = GST_VIDEO_FRAME_PLANE_DATA(f, 1);
  const gint sy = GST_VIDEO_FRAME_PLANE_STRIDE(f, 0), suv = GST_VIDEO_FRAME_PLANE_STRIDE(f, 1);

  for (gint r = 0; r < s.ch; r++) {
    gint wb, a;
    const gint zone = pb_row_zone(&s, r, &wb, &a);
    if (zone == 0 && wb == 0)
      continue;
    guint8 *row = dy + (gsize) (p->y + r) * sy + p->x;
    const gint le = MIN(zone, s.cw), rs = MAX(s.cw - zone, le);
    if (wb > 0 && rs > le) {
      const gint wbe = (wb * opf + PB_ONE / 2) >> PB_SHIFT;
      for (gint m = le; m < rs; m++)
        row[m] = (guint8) PB_RMIX((gint) row[m], yb, wbe);
    }
    for (gint pass = 0; pass < 2; pass++) {
      const gint x0 = pass ? rs : 0, x1 = pass ? s.cw : le;
      for (gint x = x0; x < x1; x++) {
        gint wbp, ap, my = 0, mu = 0, mv = 0;
        pb_shape_px(&s, x, r, &wbp, &ap);
        gint c = row[x];
        const gint wbe = (wbp * opf + PB_ONE / 2) >> PB_SHIFT;
        if (wbe > 0)
          c = PB_RMIX(c, yb, wbe);
        if (ap < PB_ONE) {
          if (mc && rd_main_px(mc, x, r, &my, &mu, &mv))
            c = PB_RMIX(my, c, ap);
          else if (black_ok)
            c = PB_RMIX(16, c, ap);
          else
            continue;
        }
        row[x] = (guint8) c;
      }
    }
  }

  for (gint mr = 0; mr < s.ch / 2; mr++) {
    gint wb0, a0, wb1, a1;
    const gint z0 = pb_row_zone(&s, 2 * mr, &wb0, &a0), z1 = pb_row_zone(&s, 2 * mr + 1, &wb1, &a1);
    const gint zone = MAX(z0, z1);
    if (zone == 0 && wb0 == 0 && wb1 == 0)
      continue;
    guint8 *row = duv + (gsize) (p->y / 2 + mr) * suv + p->x;
    const gint np = s.cw / 2, kz = (zone + 1) / 2, le = MIN(kz, np), rs = MAX(np - kz, le);
    const gint wbm = (wb0 + wb1 + 1) >> 1;
    if (wbm > 0 && rs > le) {
      const gint wbe = (wbm * opf + PB_ONE / 2) >> PB_SHIFT;
      for (gint k = le; k < rs; k++) {
        row[2 * k] = (guint8) PB_RMIX((gint) row[2 * k], ub, wbe);
        row[2 * k + 1] = (guint8) PB_RMIX((gint) row[2 * k + 1], vb, wbe);
      }
    }
    for (gint pass = 0; pass < 2; pass++) {
      const gint k0 = pass ? rs : 0, k1 = pass ? np : le;
      for (gint k = k0; k < k1; k++) {
        gint w4 = 0, a4 = 0;
        for (gint q = 0; q < 4; q++) {
          gint w, a;
          pb_shape_px(&s, 2 * k + (q & 1), 2 * mr + (q >> 1), &w, &a);
          w4 += w;
          a4 += a;
        }
        w4 = (w4 + 2) >> 2;
        a4 = (a4 + 2) >> 2;
        gint cu = row[2 * k], cv = row[2 * k + 1];
        const gint wbe = (w4 * opf + PB_ONE / 2) >> PB_SHIFT;
        if (wbe > 0) {
          cu = PB_RMIX(cu, ub, wbe);
          cv = PB_RMIX(cv, vb, wbe);
        }
        if (a4 < PB_ONE) {
          gint my = 0, mu = 0, mv = 0;
          if (mc && rd_main_px(mc, 2 * k, 2 * mr, &my, &mu, &mv)) {
            cu = PB_RMIX(mu, cu, a4);
            cv = PB_RMIX(mv, cv, a4);
          } else if (black_ok) {
            cu = PB_RMIX(128, cu, a4);
            cv = PB_RMIX(128, cv, a4);
          } else {
            continue;
          }
        }
        row[2 * k] = (guint8) cu;
        row[2 * k + 1] = (guint8) cv;
      }
    }
  }
}

static GstFlowReturn pb_round_transform_ip(GstVideoFilter *filter, GstVideoFrame *frame) {
  g_mutex_lock(&rd_lock);
  const guint64 rt = GST_BUFFER_PTS_IS_VALID(frame->buffer) ?
      gst_segment_to_running_time(&GST_BASE_TRANSFORM(filter)->segment, GST_FORMAT_TIME, GST_BUFFER_PTS(frame->buffer)) : GST_CLOCK_TIME_NONE;
  const gboolean black_ok = rd_active == 0;
  for (gint i = 0; i < RD_PIPS; i++)
    if (rd_pip[i].on)
      pb_round_pip(frame, i, &rd_pip[i], rt, black_ok);
  g_mutex_unlock(&rd_lock);
  return GST_FLOW_OK;
}

static void pb_round_class_init(PbRoundClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  GstVideoFilterClass *vc = GST_VIDEO_FILTER_CLASS(klass);
  oc->set_property = pb_round_set_property;
  oc->get_property = pb_round_get_property;
  static const struct { guint id; const gchar *name; const gchar *nick; } pp[] = {
    {PROP_RPIP1, "pip1", "Bild 1"}, {PROP_RPIP2, "pip2", "Bild 2"}, {PROP_RPIP3, "pip3", "Bild 3"}};
  for (guint i = 0; i < G_N_ELEMENTS(pp); i++)
    g_object_class_install_property(oc, pp[i].id,
        g_param_spec_string(pp[i].name, pp[i].nick, "x,y,w,h,bw,bc,bo,br,op des gerundeten kleinen Bildes; off = nicht bearbeiten", "off",
                            G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  gst_element_class_set_static_metadata(ec, "IRL4YOU Ecken runden", "Filter/Effect/Video", "Rundet kleine Bilder und zeichnet den Rahmen", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &rnd_sink);
  gst_element_class_add_static_pad_template(ec, &rnd_src);
  vc->transform_frame_ip = pb_round_transform_ip;
}

static void pb_round_init(PbRound *self) {
  gst_base_transform_set_in_place(GST_BASE_TRANSFORM(self), TRUE);
}

#define SEL_PADS 4
#define STATE_MARK G_GUINT64_CONSTANT(0x9000000000000000)

typedef struct {
  GstElement parent;
  GstPad *src;
  GstPad *sinks[SEL_PADS];
  GstCaps *caps[SEL_PADS];
  GMutex lock;
  guint state;
  gboolean tag_offset, force_key;
  gint sent_pad;
  GstCaps *sent_caps;
  gboolean sent_start, sent_seg, resync;
  gint64 offset;
  gboolean have_next;
  GstClockTime next_out, prev_pts;
  guint64 switches;
  GMutex plock;
  gboolean p_valid[SEL_PADS];
  gint64 p_avg[SEL_PADS];
  gint64 p_rt[SEL_PADS];
  gint64 dropped_ns;
  gint drop_idx;
  gboolean first_after_switch;
  gboolean fade_in;
  gboolean c_valid;
  gint64 c_ref;
  gint pend_idx;
  gint64 pend_since;
  GstBuffer *held;

  GstCaps *fill_caps;
  GThread *fill_thread;
  volatile gint fill_run;
  gint64 last_push_rt;
  gboolean after_fill;
  gboolean filling;
  gint64 fill_rt0;
  GstClockTime fill_out0;
} PbPipSel;
typedef struct { GstElementClass parent_class; } PbPipSelClass;
G_DEFINE_TYPE(PbPipSel, pb_pip_sel, GST_TYPE_ELEMENT)

enum { SEL_0, SEL_STATE, SEL_TAG, SEL_KEY, SEL_FILL };

static GMutex pb_live_lock;
static gint64 pb_live_rt[SEL_PADS] = { -1, -1, -1, -1 };
#define LIVE_DEAD_NS (1500 * GST_MSECOND)
#define FILL_VIDEO_AFTER (500 * GST_MSECOND)
#define FILL_AUDIO_AFTER (150 * GST_MSECOND)
#define FILL_DEAD (700 * GST_MSECOND)
#define FILL_AUDIO_LONG (3 * GST_SECOND)

static void pb_live_touch(gint idx, gint64 rt) {
  if (idx < 0 || idx >= SEL_PADS)
    return;
  g_mutex_lock(&pb_live_lock);
  pb_live_rt[idx] = rt;
  g_mutex_unlock(&pb_live_lock);
}

static gint64 pb_live_get(gint idx) {
  if (idx < 0 || idx >= SEL_PADS)
    return -1;
  g_mutex_lock(&pb_live_lock);
  const gint64 v = pb_live_rt[idx];
  g_mutex_unlock(&pb_live_lock);
  return v;
}

static void pb_live_reset(void) {
  g_mutex_lock(&pb_live_lock);
  for (guint i = 0; i < SEL_PADS; i++)
    pb_live_rt[i] = -1;
  g_mutex_unlock(&pb_live_lock);
}

static GstStaticPadTemplate sel_sink_tmpl = GST_STATIC_PAD_TEMPLATE("sink_%u", GST_PAD_SINK, GST_PAD_REQUEST, GST_STATIC_CAPS_ANY);
static GstStaticPadTemplate sel_src_tmpl = GST_STATIC_PAD_TEMPLATE("src", GST_PAD_SRC, GST_PAD_ALWAYS, GST_STATIC_CAPS_ANY);

static void pb_sel_reset(PbPipSel *self) {
  g_mutex_lock(&self->lock);
  self->sent_pad = -1;
  self->sent_start = self->sent_seg = self->resync = self->have_next = FALSE;
  self->offset = 0;
  self->dropped_ns = 0;
  self->drop_idx = -1;
  self->first_after_switch = FALSE;
  self->fade_in = FALSE;
  self->c_valid = FALSE;
  self->pend_idx = -1;
  gst_clear_buffer(&self->held);
  self->next_out = self->prev_pts = GST_CLOCK_TIME_NONE;
  self->last_push_rt = -1;
  self->after_fill = self->filling = FALSE;
  gst_caps_replace(&self->sent_caps, NULL);
  for (guint i = 0; i < SEL_PADS; i++)
    gst_caps_replace(&self->caps[i], NULL);
  g_mutex_unlock(&self->lock);
  g_mutex_lock(&self->plock);
  for (guint i = 0; i < SEL_PADS; i++)
    self->p_valid[i] = FALSE;
  g_mutex_unlock(&self->plock);
}

static gboolean pb_sel_push_sticky_locked(PbPipSel *self, gint idx) {
  if (!self->sent_start) {
    gchar *sid = g_strdup_printf("pbpipsel/%p", (void *) self);
    GstEvent *ev = gst_event_new_stream_start(sid);
    g_free(sid);
    gst_event_set_group_id(ev, gst_util_group_id_next());
    if (!gst_pad_push_event(self->src, ev))
      return FALSE;
    self->sent_start = TRUE;
  }
  if (self->caps[idx] && (!self->sent_caps || !gst_caps_is_equal(self->sent_caps, self->caps[idx]))) {
    if (!gst_pad_push_event(self->src, gst_event_new_caps(self->caps[idx])))
      return FALSE;
    gst_caps_replace(&self->sent_caps, self->caps[idx]);
  }
  if (!self->sent_seg) {
    GstSegment seg;
    gst_segment_init(&seg, GST_FORMAT_TIME);
    if (!gst_pad_push_event(self->src, gst_event_new_segment(&seg)))
      return FALSE;
    self->sent_seg = TRUE;
  }
  return TRUE;
}

static gint64 pb_sel_running_time(GstElement *el) {
  GstClock *c = gst_element_get_clock(el);
  if (!c)
    return -1;
  const gint64 r = (gint64) gst_clock_get_time(c) - (gint64) gst_element_get_base_time(el);
  gst_object_unref(c);
  return r;
}

#define SEL_P_TAU (10 * GST_SECOND)
#define SEL_MAX_AHEAD (5 * GST_SECOND)
#define SEL_MAX_DROP (3 * GST_SECOND)
#define SEL_TOLERANCE (400 * GST_MSECOND)
#define SEL_HOLE (50 * GST_MSECOND)
#define SEL_MAX_WAIT (2500 * GST_MSECOND)

static void pb_sel_sample(PbPipSel *self, gint idx, GstClockTime pts) {
  if (!GST_CLOCK_TIME_IS_VALID(pts))
    return;
  const gint64 rt = pb_sel_running_time((GstElement *) self);
  if (rt < 0)
    return;
  const gint64 sample = (gint64) pts - rt;
  g_mutex_lock(&self->plock);
  if (!self->p_valid[idx]) {
    self->p_valid[idx] = TRUE;
    self->p_avg[idx] = sample;
  } else {

    const gint64 dt = rt > self->p_rt[idx] ? rt - self->p_rt[idx] : 0;
    gdouble alpha = (gdouble) dt / (gdouble) SEL_P_TAU;
    if (alpha > 0.2)
      alpha = 0.2;
    self->p_avg[idx] += (gint64) ((gdouble) (sample - self->p_avg[idx]) * alpha);
  }
  self->p_rt[idx] = rt;
  g_mutex_unlock(&self->plock);
}

static gboolean pb_sel_audio_fmt(PbPipSel *self, gint idx, gint *rate, gint *ch) {
  if (!self->caps[idx] || gst_caps_get_size(self->caps[idx]) < 1)
    return FALSE;
  const GstStructure *st = gst_caps_get_structure(self->caps[idx], 0);
  const gchar *fmt = gst_structure_get_string(st, "format");
  if (g_strcmp0(gst_structure_get_name(st), "audio/x-raw") != 0 || g_strcmp0(fmt, "S16LE") != 0)
    return FALSE;
  if (!gst_structure_get_int(st, "rate", rate) || !gst_structure_get_int(st, "channels", ch) || *rate < 8000 || *ch < 1 || *ch > 8)
    return FALSE;
  return TRUE;
}

static GstBuffer *pb_sel_fade(GstBuffer *buf, gboolean fade_in, gint ch) {
  buf = gst_buffer_make_writable(buf);
  GstMapInfo m;
  if (!gst_buffer_map(buf, &m, GST_MAP_READWRITE))
    return buf;
  const gsize frames = m.size / (2 * (gsize) ch);
  gint16 *x = (gint16 *) m.data;
  for (gsize i = 0; i < frames; i++) {
    const gdouble g = frames > 1 ? (gdouble) i / (gdouble) (frames - 1) : 1.0;
    const gdouble k = fade_in ? g : 1.0 - g;
    for (gint c = 0; c < ch; c++)
      x[i * ch + c] = (gint16) (x[i * ch + c] * k);
  }
  gst_buffer_unmap(buf, &m);
  return buf;
}

#define SEL_SILENCE_MAX (12 * GST_SECOND)
#define SEL_SILENCE_MIN (5 * GST_MSECOND)

static GstFlowReturn pb_sel_gap_locked(PbPipSel *self, gint rate, gint ch, GstClockTime to) {
  GstFlowReturn ret = GST_FLOW_OK;
  if (self->held) {
    GstBuffer *h = pb_sel_fade(self->held, FALSE, ch);
    self->held = NULL;
    ret = gst_pad_push(self->src, h);
    if (ret != GST_FLOW_OK)
      return ret;
  }
  if (!self->have_next || !GST_CLOCK_TIME_IS_VALID(to) || to <= self->next_out + SEL_SILENCE_MIN)
    return ret;
  if (to - self->next_out > SEL_SILENCE_MAX)
    to = self->next_out + SEL_SILENCE_MAX;
  while (self->next_out + SEL_SILENCE_MIN < to) {
    GstClockTime chunk = MIN(to - self->next_out, 20 * GST_MSECOND);
    const gsize frames = (gsize) gst_util_uint64_scale(chunk, (guint64) rate, GST_SECOND);
    if (frames == 0)
      break;
    GstBuffer *b = gst_buffer_new_and_alloc(frames * 2 * (gsize) ch);
    GstMapInfo m;
    if (gst_buffer_map(b, &m, GST_MAP_WRITE)) {
      memset(m.data, 0, m.size);
      gst_buffer_unmap(b, &m);
    }
    chunk = gst_util_uint64_scale(frames, GST_SECOND, (guint64) rate);
    GST_BUFFER_PTS(b) = self->next_out;
    GST_BUFFER_DTS(b) = self->next_out;
    GST_BUFFER_DURATION(b) = chunk;
    if (self->resync) {
      GST_BUFFER_FLAG_SET(b, GST_BUFFER_FLAG_DISCONT);
      self->resync = FALSE;
    }
    self->next_out += chunk;
    ret = gst_pad_push(self->src, b);
    if (ret != GST_FLOW_OK)
      return ret;
  }
  return ret;
}

static gboolean pb_sel_push_sticky_fill_locked(PbPipSel *self, GstCaps *caps) {
  if (!self->sent_start) {
    gchar *sid = g_strdup_printf("pbpipsel/%p", (void *) self);
    GstEvent *ev = gst_event_new_stream_start(sid);
    g_free(sid);
    gst_event_set_group_id(ev, gst_util_group_id_next());
    if (!gst_pad_push_event(self->src, ev))
      return FALSE;
    self->sent_start = TRUE;
  }
  if (!self->sent_caps || !gst_caps_is_equal(self->sent_caps, caps)) {
    if (!gst_pad_push_event(self->src, gst_event_new_caps(caps)))
      return FALSE;
    gst_caps_replace(&self->sent_caps, caps);
  }
  if (!self->sent_seg) {
    GstSegment seg;
    gst_segment_init(&seg, GST_FORMAT_TIME);
    if (!gst_pad_push_event(self->src, gst_event_new_segment(&seg)))
      return FALSE;
    self->sent_seg = TRUE;
  }
  return TRUE;
}

static void pb_sel_fill_video(PbPipSel *self, gint64 rt) {
  g_mutex_lock(&self->lock);
  GstCaps *caps = self->sent_caps ? self->sent_caps : self->fill_caps;
  GstVideoInfo vi;
  if (!caps || !gst_video_info_from_caps(&vi, caps) || GST_VIDEO_INFO_FORMAT(&vi) != GST_VIDEO_FORMAT_NV12) {
    g_mutex_unlock(&self->lock);
    return;
  }
  if (self->last_push_rt < 0 && !self->filling) {
    self->last_push_rt = rt;
    g_mutex_unlock(&self->lock);
    return;
  }
  if (!self->filling && (GstClockTime) (rt - self->last_push_rt) < FILL_VIDEO_AFTER) {
    g_mutex_unlock(&self->lock);
    return;
  }
  gint fn = GST_VIDEO_INFO_FPS_N(&vi), fd = GST_VIDEO_INFO_FPS_D(&vi);
  if (fn <= 0 || fd <= 0) {
    fn = 30;
    fd = 1;
  }
  const GstClockTime dur = gst_util_uint64_scale(GST_SECOND, (guint64) fd, (guint64) fn);
  if (!pb_sel_push_sticky_fill_locked(self, caps)) {
    g_mutex_unlock(&self->lock);
    return;
  }
  const guint state = g_atomic_int_get(&self->state);
  if (!self->have_next) {
    self->next_out = (GstClockTime) rt;
    self->have_next = TRUE;
  }
  const gint64 target = rt + self->offset;
  for (guint n = 0; n < 30 && (gint64) self->next_out <= target; n++) {
    GstBuffer *b = gst_buffer_new_and_alloc(GST_VIDEO_INFO_SIZE(&vi));
    GstMapInfo m;
    if (!gst_buffer_map(b, &m, GST_MAP_WRITE)) {
      gst_buffer_unref(b);
      break;
    }
    const gsize y_off = GST_VIDEO_INFO_PLANE_OFFSET(&vi, 0), uv_off = GST_VIDEO_INFO_PLANE_OFFSET(&vi, 1);
    memset(m.data + y_off, 16, uv_off - y_off);
    memset(m.data + uv_off, 128, m.size - uv_off);
    gst_buffer_unmap(b, &m);
    GST_BUFFER_PTS(b) = self->next_out;
    GST_BUFFER_DTS(b) = self->next_out;
    GST_BUFFER_DURATION(b) = dur;
    if (self->tag_offset)
      GST_BUFFER_OFFSET(b) = STATE_MARK | (guint64) (state & 0xFFFF);
    if (self->resync) {
      GST_BUFFER_FLAG_SET(b, GST_BUFFER_FLAG_DISCONT);
      self->resync = FALSE;
    }
    self->next_out += dur;
    self->filling = TRUE;
    self->after_fill = TRUE;
    if (gst_pad_push(self->src, b) != GST_FLOW_OK)
      break;
  }
  g_mutex_unlock(&self->lock);
}

static void pb_sel_fill_audio(PbPipSel *self, gint64 rt) {
  g_mutex_lock(&self->lock);
  GstCaps *caps = self->sent_caps ? self->sent_caps : self->fill_caps;
  gint rate = 0, ch = 0;
  const GstStructure *st = caps && gst_caps_get_size(caps) ? gst_caps_get_structure(caps, 0) : NULL;
  if (!st || g_strcmp0(gst_structure_get_name(st), "audio/x-raw") != 0 || g_strcmp0(gst_structure_get_string(st, "format"), "S16LE") != 0 ||
      !gst_structure_get_int(st, "rate", &rate) || !gst_structure_get_int(st, "channels", &ch) || rate < 8000 || ch < 1 || ch > 8) {
    g_mutex_unlock(&self->lock);
    return;
  }
  if (self->last_push_rt < 0 && !self->filling) {
    self->last_push_rt = rt;
    g_mutex_unlock(&self->lock);
    return;
  }
  const gint target = (gint) (g_atomic_int_get(&self->state) & 0xF);
  const gint64 vl = pb_live_get(target);
  const gboolean cam_dead = vl >= 0 && (GstClockTime) (rt - vl) > FILL_DEAD;
  const GstClockTime quiet = (GstClockTime) (rt - self->last_push_rt);
  if (!self->filling && !((cam_dead && quiet >= FILL_AUDIO_AFTER) || quiet >= FILL_AUDIO_LONG)) {
    g_mutex_unlock(&self->lock);
    return;
  }
  if (!pb_sel_push_sticky_fill_locked(self, caps)) {
    g_mutex_unlock(&self->lock);
    return;
  }
  if (!self->filling) {
    self->filling = TRUE;
    self->fill_rt0 = rt;
    if (!self->have_next) {
      self->next_out = (GstClockTime) rt;
      self->have_next = TRUE;
    }
    self->fill_out0 = self->next_out;
  }
  self->after_fill = TRUE;
  const GstClockTime to = self->fill_out0 + (GstClockTime) (rt - self->fill_rt0);
  pb_sel_gap_locked(self, rate, ch, to);
  g_mutex_unlock(&self->lock);
}

static gpointer pb_sel_fill_thread(gpointer data) {
  PbPipSel *self = data;
  while (g_atomic_int_get(&self->fill_run)) {
    g_usleep(10000);
    const gint64 rt = pb_sel_running_time((GstElement *) self);
    if (rt < 0 || !self->fill_caps)
      continue;
    if (self->tag_offset)
      pb_sel_fill_video(self, rt);
    else
      pb_sel_fill_audio(self, rt);
  }
  return NULL;
}

static GstFlowReturn pb_sel_chain(GstPad *pad, GstObject *parent, GstBuffer *buf) {
  PbPipSel *self = (PbPipSel *) parent;
  const gint idx = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(pad), "pbsel-idx"));
  if (self->tag_offset) {
    const gint64 rt0 = pb_sel_running_time((GstElement *) self);
    if (rt0 >= 0)
      pb_live_touch(idx, rt0);
  }
  if (!self->tag_offset)
    pb_sel_sample(self, idx, GST_BUFFER_PTS(buf));

  const gboolean old_playing = !self->tag_offset && g_atomic_int_get(&self->sent_pad) == idx;
  if ((gint) (g_atomic_int_get(&self->state) & 0xF) != idx && !old_playing) {
    gst_buffer_unref(buf);
    return GST_FLOW_OK;
  }
  g_mutex_lock(&self->lock);
  const guint state = g_atomic_int_get(&self->state);
  const gint target = (gint) (state & 0xF);
  gint a_rate = 0, a_ch = 0;
  const gboolean audio_fx = !self->tag_offset && self->caps[idx] && pb_sel_audio_fmt(self, idx, &a_rate, &a_ch);
  if ((target != idx && !(audio_fx && self->sent_pad == idx)) || !self->caps[idx]) {
    g_mutex_unlock(&self->lock);
    gst_buffer_unref(buf);
    return GST_FLOW_OK;
  }
  if (target == self->sent_pad)
    self->pend_idx = -1;
  if (self->sent_pad != idx) {
    if (!pb_sel_push_sticky_locked(self, idx)) {
      g_mutex_unlock(&self->lock);
      gst_buffer_unref(buf);
      return GST_FLOW_NOT_NEGOTIATED;
    }
    const GstClockTime pts = GST_BUFFER_PTS(buf);
    if (self->sent_pad >= 0) {
      gint64 offset = self->have_next && GST_CLOCK_TIME_IS_VALID(pts) ? (gint64) self->next_out - (gint64) pts : self->offset;
      if (self->have_next && GST_CLOCK_TIME_IS_VALID(pts) && audio_fx) {

        const gint64 rt = pb_sel_running_time((GstElement *) self);
        g_mutex_lock(&self->plock);
        const gboolean ok = self->p_valid[self->sent_pad] && self->p_valid[idx];
        const gint64 p_old = self->p_avg[self->sent_pad], p_new = self->p_avg[idx];
        g_mutex_unlock(&self->plock);
        if (ok && rt >= 0) {
          if (!self->c_valid) {
            self->c_ref = self->offset + p_old;
            self->c_valid = TRUE;
          }
          if (self->pend_idx != idx) {
            self->pend_idx = idx;
            self->pend_since = rt;
          }
          const gint64 out_ideal = (gint64) pts + self->c_ref - p_new;
          const gint64 diff = out_ideal - (gint64) self->next_out;
          const gboolean waited_long = rt - self->pend_since > (gint64) SEL_MAX_WAIT;

          if ((diff > (gint64) SEL_HOLE || diff < -(gint64) SEL_TOLERANCE) && !waited_long) {
            g_mutex_unlock(&self->lock);
            gst_buffer_unref(buf);
            return GST_FLOW_OK;
          }
          if (waited_long) {
            if (diff > (gint64) SEL_HOLE)
              offset = self->c_ref - p_new;
            else
              self->c_valid = FALSE;
          }
          const GstFlowReturn fr = pb_sel_gap_locked(self, a_rate, a_ch, offset == self->c_ref - p_new && diff > (gint64) SEL_HOLE ? (GstClockTime) MAX(out_ideal, 0) : GST_CLOCK_TIME_NONE);
          if (fr != GST_FLOW_OK) {
            g_mutex_unlock(&self->lock);
            gst_buffer_unref(buf);
            return fr;
          }
          self->fade_in = TRUE;
        }
        self->pend_idx = -1;
      }
      self->offset = offset;
      self->resync = TRUE;
      if (self->force_key)
        gst_pad_push_event(self->src, gst_video_event_new_downstream_force_key_unit(
            GST_CLOCK_TIME_NONE, GST_CLOCK_TIME_NONE, GST_CLOCK_TIME_NONE, TRUE, 0));
      self->switches++;
    }
    self->sent_pad = idx;
    self->dropped_ns = 0;
    self->prev_pts = GST_CLOCK_TIME_NONE;
  } else if (!self->sent_caps || (self->caps[idx] && !gst_caps_is_equal(self->sent_caps, self->caps[idx]))) {
    pb_sel_push_sticky_locked(self, idx);
  }
  buf = gst_buffer_make_writable(buf);
  const GstClockTime pts = GST_BUFFER_PTS(buf), dts = GST_BUFFER_DTS(buf);
  if (GST_CLOCK_TIME_IS_VALID(pts)) {
    gint64 o = (gint64) pts + self->offset;
    if (o < 0)
      o = 0;
    if (self->first_after_switch) {
      if (self->have_next && o < (gint64) self->next_out)
        o = (gint64) self->next_out;
      self->first_after_switch = FALSE;
    }
    if (self->after_fill) {
      if (self->have_next && o < (gint64) self->next_out)
        o = (gint64) self->next_out;
      else if (audio_fx && self->have_next && o > (gint64) self->next_out + SEL_SILENCE_MIN) {
        const GstFlowReturn gr = pb_sel_gap_locked(self, a_rate, a_ch, (GstClockTime) o);
        if (gr != GST_FLOW_OK) {
          g_mutex_unlock(&self->lock);
          gst_buffer_unref(buf);
          return gr;
        }
      }
      self->after_fill = FALSE;
    }
    GST_BUFFER_PTS(buf) = (GstClockTime) o;
    GstClockTime dur = 0;
    if (GST_BUFFER_DURATION_IS_VALID(buf))
      dur = GST_BUFFER_DURATION(buf);
    else if (GST_CLOCK_TIME_IS_VALID(self->prev_pts) && pts > self->prev_pts && pts - self->prev_pts < 200 * GST_MSECOND)
      dur = pts - self->prev_pts;
    self->prev_pts = pts;
    self->next_out = (GstClockTime) o + dur;
    self->have_next = TRUE;
  }
  if (GST_CLOCK_TIME_IS_VALID(dts)) {
    gint64 o = (gint64) dts + self->offset;
    GST_BUFFER_DTS(buf) = o < 0 ? 0 : (GstClockTime) o;
  }
  if (self->resync) {
    GST_BUFFER_FLAG_SET(buf, GST_BUFFER_FLAG_DISCONT);
    self->resync = FALSE;
  }
  if (self->tag_offset)
    GST_BUFFER_OFFSET(buf) = STATE_MARK | (guint64) (state & 0xFFFF);
  GstFlowReturn ret;
  self->filling = FALSE;
  self->last_push_rt = pb_sel_running_time((GstElement *) self);
  if (audio_fx) {
    if (self->fade_in) {
      buf = pb_sel_fade(buf, TRUE, a_ch);
      self->fade_in = FALSE;
    }
    GstBuffer *prev = self->held;
    self->held = buf;
    ret = prev ? gst_pad_push(self->src, prev) : GST_FLOW_OK;
  } else {
    if (self->held) {
      GstBuffer *prev = self->held;
      self->held = NULL;
      gst_pad_push(self->src, prev);
    }
    ret = gst_pad_push(self->src, buf);
  }
  g_mutex_unlock(&self->lock);
  return ret;
}

static gboolean pb_sel_event(GstPad *pad, GstObject *parent, GstEvent *event) {
  PbPipSel *self = (PbPipSel *) parent;
  const gint idx = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(pad), "pbsel-idx"));
  const gboolean act = (gint) (g_atomic_int_get(&self->state) & 0xF) == idx;
  gboolean ret = TRUE;
  switch (GST_EVENT_TYPE(event)) {
    case GST_EVENT_CAPS: {
      GstCaps *c = NULL;
      gst_event_parse_caps(event, &c);
      g_mutex_lock(&self->lock);
      gst_caps_replace(&self->caps[idx], c);
      g_mutex_unlock(&self->lock);
      gst_event_unref(event);
      break;
    }
    case GST_EVENT_EOS:
      if (act) {
        g_mutex_lock(&self->lock);
        if (self->sent_pad < 0 && self->caps[idx])
          pb_sel_push_sticky_locked(self, idx);
        if (self->held) {
          GstBuffer *h = self->held;
          self->held = NULL;
          gst_pad_push(self->src, h);
        }
        ret = gst_pad_push_event(self->src, event);
        g_mutex_unlock(&self->lock);
      } else {
        gst_event_unref(event);
      }
      break;
    case GST_EVENT_FLUSH_START:
      if (act) {
        ret = gst_pad_push_event(self->src, event);
        g_mutex_lock(&self->lock);
        gst_clear_buffer(&self->held);
        g_mutex_unlock(&self->lock);
      } else {
        gst_event_unref(event);
      }
      break;
    case GST_EVENT_FLUSH_STOP:
      if (act) {
        g_mutex_lock(&self->lock);
        self->sent_seg = FALSE;
        g_mutex_unlock(&self->lock);
        ret = gst_pad_push_event(self->src, event);
      } else {
        gst_event_unref(event);
      }
      break;
    default:
      gst_event_unref(event);
      break;
  }
  return ret;
}

static GstPad *pb_sel_request_pad(GstElement *el, GstPadTemplate *templ, const gchar *name, const GstCaps *caps) {
  PbPipSel *self = (PbPipSel *) el;
  guint idx = 0;
  gchar tail = 0;
  if (!name || sscanf(name, "sink_%u%c", &idx, &tail) != 1 || idx >= SEL_PADS || self->sinks[idx])
    return NULL;
  GstPad *pad = gst_pad_new_from_template(templ, name);
  g_object_set_data(G_OBJECT(pad), "pbsel-idx", GINT_TO_POINTER((gint) idx));
  gst_pad_set_chain_function(pad, pb_sel_chain);
  gst_pad_set_event_function(pad, pb_sel_event);
  gst_pad_set_active(pad, TRUE);
  gst_element_add_pad(el, pad);
  self->sinks[idx] = pad;
  return pad;
}

static void pb_sel_release_pad(GstElement *el, GstPad *pad) {
  PbPipSel *self = (PbPipSel *) el;
  const gint idx = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(pad), "pbsel-idx"));
  if (idx >= 0 && idx < SEL_PADS && self->sinks[idx] == pad)
    self->sinks[idx] = NULL;
  gst_pad_set_active(pad, FALSE);
  gst_element_remove_pad(el, pad);
}

static GstStateChangeReturn pb_sel_change_state(GstElement *el, GstStateChange t) {
  PbPipSel *self = (PbPipSel *) el;
  if (t == GST_STATE_CHANGE_READY_TO_PAUSED) {
    pb_sel_reset(self);
    if (self->tag_offset)
      pb_live_reset();
  }
  if (t == GST_STATE_CHANGE_PAUSED_TO_PLAYING && !self->fill_thread && self->fill_caps) {
    g_atomic_int_set(&self->fill_run, 1);
    self->fill_thread = g_thread_new("pbsel-fill", pb_sel_fill_thread, self);
  }
  GstStateChangeReturn r = GST_ELEMENT_CLASS(pb_pip_sel_parent_class)->change_state(el, t);
  if ((t == GST_STATE_CHANGE_PLAYING_TO_PAUSED || t == GST_STATE_CHANGE_READY_TO_NULL) && self->fill_thread) {
    g_atomic_int_set(&self->fill_run, 0);
    g_thread_join(self->fill_thread);
    self->fill_thread = NULL;
  }
  return r;
}

static void pb_sel_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbPipSel *self = (PbPipSel *) o;
  switch (id) {
    case SEL_STATE: g_atomic_int_set(&self->state, g_value_get_uint(v)); break;
    case SEL_TAG: self->tag_offset = g_value_get_boolean(v); break;
    case SEL_KEY: self->force_key = g_value_get_boolean(v); break;
    case SEL_FILL: {
      const gchar *txt = g_value_get_string(v);
      GstCaps *c = txt && *txt ? gst_caps_from_string(txt) : NULL;
      g_mutex_lock(&self->lock);
      gst_caps_replace(&self->fill_caps, c);
      g_mutex_unlock(&self->lock);
      if (c)
        gst_caps_unref(c);
      break;
    }
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

static void pb_sel_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbPipSel *self = (PbPipSel *) o;
  switch (id) {
    case SEL_STATE: g_value_set_uint(v, g_atomic_int_get(&self->state)); break;
    case SEL_TAG: g_value_set_boolean(v, self->tag_offset); break;
    case SEL_KEY: g_value_set_boolean(v, self->force_key); break;
    case SEL_FILL: {
      g_mutex_lock(&self->lock);
      gchar *t = self->fill_caps ? gst_caps_to_string(self->fill_caps) : NULL;
      g_mutex_unlock(&self->lock);
      g_value_take_string(v, t);
      break;
    }
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
  }
}

static void pb_sel_finalize(GObject *o) {
  PbPipSel *self = (PbPipSel *) o;
  gst_caps_replace(&self->sent_caps, NULL);
  for (guint i = 0; i < SEL_PADS; i++)
    gst_caps_replace(&self->caps[i], NULL);
  gst_clear_buffer(&self->held);
  gst_caps_replace(&self->fill_caps, NULL);
  g_mutex_clear(&self->lock);
  g_mutex_clear(&self->plock);
  G_OBJECT_CLASS(pb_pip_sel_parent_class)->finalize(o);
}

static void pb_pip_sel_class_init(PbPipSelClass *klass) {
  GObjectClass *oc = G_OBJECT_CLASS(klass);
  GstElementClass *ec = GST_ELEMENT_CLASS(klass);
  oc->set_property = pb_sel_set_property;
  oc->get_property = pb_sel_get_property;
  oc->finalize = pb_sel_finalize;
  g_object_class_install_property(oc, SEL_STATE,
      g_param_spec_uint("state", "Zustand", "untere 4 Bit: gewählter Eingang; danach je 4 Bit: Kamera an Stelle 1, 2, 3 (15 = keine)",
                        0, G_MAXUINT, 0, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | GST_PARAM_MUTABLE_PLAYING));
  g_object_class_install_property(oc, SEL_TAG,
      g_param_spec_boolean("tag-offset", "Zustand ins Bild schreiben", "Zustand in GST_BUFFER_OFFSET jedes Bildes eintragen (für pbpipmix)",
                           FALSE, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, SEL_KEY,
      g_param_spec_boolean("force-key", "Bildanfang erzwingen", "beim Umschalten dem Encoder einen vollständigen Bildanfang nahelegen",
                           FALSE, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, SEL_FILL,
      g_param_spec_string("fill-caps", "Format zum Füllen", "Format (Bild: video/x-raw NV12 mit Größe und Bildrate; Ton: audio/x-raw S16LE), mit dem der Ausgang bei fehlendem Eingang mit "
                          "schwarzen Bildern / Stille am Leben gehalten wird. Leer: kein Füllen.", NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  gst_element_class_set_static_metadata(ec, "IRL4YOU PiP Umschalter", "Generic",
      "Wählt einen von bis zu vier Eingängen, ohne den Datenstrom zu unterbrechen", "IRL4YOU");
  gst_element_class_add_static_pad_template(ec, &sel_sink_tmpl);
  gst_element_class_add_static_pad_template(ec, &sel_src_tmpl);
  ec->request_new_pad = pb_sel_request_pad;
  ec->release_pad = pb_sel_release_pad;
  ec->change_state = pb_sel_change_state;
}

static void pb_pip_sel_init(PbPipSel *self) {
  g_mutex_init(&self->lock);
  g_mutex_init(&self->plock);
  self->sent_pad = -1;
  self->pend_idx = -1;
  self->last_push_rt = -1;
  self->next_out = self->prev_pts = GST_CLOCK_TIME_NONE;
  self->src = gst_pad_new_from_static_template(&sel_src_tmpl, "src");
  gst_element_add_pad(GST_ELEMENT(self), self->src);
}

typedef struct {
  GstElement parent;
  gchar *file, *video_queue, *audio_queue, *pip_queue, *pip2_queue, *pip3_queue;
  gchar *cam[4];
  gchar *selector, *audio_selector, *select_file, *state_file;
  gint audio_pos;
  gchar *view_file, *view_state_file, *mixer_name, *volume_name;
  gchar *live_file;
  gchar live_last[16];
  gint64 live_written_rt;
  gchar *style_base[3];
  gboolean view_inited;
  gint view_hide;
  gint last_v[4];
  gboolean have_v;
  GThread *thread;
  volatile gint run;
  gint64 last_mtime, sel_mtime, view_mtime;
} PbCtl;
typedef struct { GstElementClass parent_class; } PbCtlClass;
G_DEFINE_TYPE(PbCtl, pb_ctl, GST_TYPE_ELEMENT)

enum { CTL_0, CTL_FILE, CTL_VQ, CTL_AQ, CTL_PQ, CTL_P2Q, CTL_P3Q, CTL_CAM0, CTL_CAM1, CTL_CAM2, CTL_CAM3,
       CTL_SEL, CTL_ASEL, CTL_SELFILE, CTL_STATEFILE, CTL_VIEWFILE, CTL_VIEWSTATE, CTL_MIXER, CTL_VOLUME, CTL_APOS, CTL_LIVE };

static void pb_ctl_set_queue(PbCtl *self, GstObject *par, const gchar *name, gint ms, gboolean small, gint extra) {
  if (!name || !*name || ms < 0 || ms > 3000)
    return;
  GstElement *q = gst_bin_get_by_name(GST_BIN(par), name);
  if (!q)
    return;

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

static void pb_ctl_set_cam(PbCtl *self, GstObject *par, const gchar *spec, gint ms) {
  if (!spec || !*spec)
    return;
  gchar **items = g_strsplit(spec, ",", 0);
  for (gchar **it = items; *it; it++) {
    gchar *colon = strchr(*it, ':');
    if (!colon || !colon[1])
      continue;
    *colon = 0;
    if (colon[1] == 'v') pb_ctl_set_queue(self, par, *it, ms, FALSE, 33);
    else if (colon[1] == 's') pb_ctl_set_queue(self, par, *it, ms, TRUE, 33);
    else if (colon[1] == 'a') pb_ctl_set_queue(self, par, *it, ms, FALSE, 0);
  }
  g_strfreev(items);
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
  const gint ms[4] = { main_ms, pip_ms, pip2_ms, pip3_ms };
  for (guint i = 0; i < 4; i++)
    pb_ctl_set_cam(self, par, self->cam[i], ms[i]);
  gst_object_unref(par);
}

static void pb_ctl_publish(PbCtl *self, const int v[4]) {
  if (!self->state_file || !*self->state_file)
    return;
  gchar *txt = g_strdup_printf("%d %d %d %d\n", v[0], v[1], v[2], v[3]);
  if (g_file_set_contents(self->state_file, txt, -1, NULL))
    g_chmod(self->state_file, 0644);
  g_free(txt);
}

static void pb_ctl_apply_select(PbCtl *self, const int v[4]) {
  GstObject *par = gst_object_get_parent(GST_OBJECT(self));
  if (!par)
    return;
  const guint st = (guint) ((v[0] & 0xF) | ((v[1] & 0xF) << 4) | ((v[2] & 0xF) << 8) | ((v[3] & 0xF) << 12));
  for (guint i = 0; i < 4; i++)
    self->last_v[i] = v[i];
  self->have_v = TRUE;
  GstElement *sel = self->selector ? gst_bin_get_by_name(GST_BIN(par), self->selector) : NULL;
  if (sel) {
    g_object_set(sel, "state", st, NULL);
    gst_object_unref(sel);
  }
  GstElement *asel = self->audio_selector ? gst_bin_get_by_name(GST_BIN(par), self->audio_selector) : NULL;
  if (asel) {
    const gint cam = self->audio_pos < 0 ? v[0] : v[1 + MIN(self->audio_pos, 2)];
    if (cam >= 0 && cam <= 3)
      g_object_set(asel, "state", (guint) cam, NULL);
    gst_object_unref(asel);
  }
  pb_ctl_publish(self, v);
  GST_INFO_OBJECT(self, "Umschalter: %d %d %d %d", v[0], v[1], v[2], v[3]);
  gst_object_unref(par);
}

static void pb_ctl_poll_select(PbCtl *self, gboolean first) {
  if (!self->selector || !*self->selector)
    return;
  struct stat st;
  if (self->select_file && stat(self->select_file, &st) == 0) {
    gint64 mt = (gint64) st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
    if (mt != self->sel_mtime) {
      self->sel_mtime = mt;
      int v[4] = { -1, 15, 15, 15 };
      FILE *f = fopen(self->select_file, "r");
      if (f) {
        if (fscanf(f, "%d %d %d %d", &v[0], &v[1], &v[2], &v[3]) < 1)
          v[0] = -1;
        fclose(f);
      }
      gboolean ok = v[0] >= 0 && v[0] <= 3;
      for (guint i = 1; i < 4; i++)
        ok = ok && v[i] >= 0 && v[i] <= 15;
      if (ok) {
        pb_ctl_apply_select(self, v);
        return;
      }
    }
  }
  if (first) {
    GstObject *par = gst_object_get_parent(GST_OBJECT(self));
    GstElement *sel = par ? gst_bin_get_by_name(GST_BIN(par), self->selector) : NULL;
    if (sel) {
      guint s = 0;
      g_object_get(sel, "state", &s, NULL);
      int v[4] = { (int) (s & 0xF), (int) ((s >> 4) & 0xF), (int) ((s >> 8) & 0xF), (int) ((s >> 12) & 0xF) };
      for (guint i = 0; i < 4; i++)
        self->last_v[i] = v[i];
      self->have_v = TRUE;
      pb_ctl_publish(self, v);
      gst_object_unref(sel);
    }
    if (par)
      gst_object_unref(par);
  }
}

static gchar *pb_style_strip_op(const gchar *text) {
  GString *out = g_string_new(NULL);
  gchar **parts = g_strsplit(text ? text : "", ",", 0);
  for (gchar **p = parts; *p; p++) {
    gchar *eq = strchr(*p, '=');
    if (eq) {
      gchar *key = g_strndup(*p, (gsize) (eq - *p));
      g_strstrip(key);
      const gboolean is_op = g_strcmp0(key, "op") == 0;
      g_free(key);
      if (is_op)
        continue;
    }
    gchar *t = g_strstrip(*p);
    if (!*t)
      continue;
    if (out->len)
      g_string_append_c(out, ',');
    g_string_append(out, t);
  }
  g_strfreev(parts);
  return g_string_free(out, FALSE);
}

static gboolean pb_style_is_hidden(const gchar *text) {
  PbStyle st;
  pb_style_parse(text, &st);
  return st.op == 0;
}

static void pb_ctl_publish_view(PbCtl *self, gint hide, gint audio, gint mute) {
  if (!self->view_state_file || !*self->view_state_file)
    return;
  gchar *txt = g_strdup_printf("%d %d %d\n", hide, audio, mute);
  if (g_file_set_contents(self->view_state_file, txt, -1, NULL))
    g_chmod(self->view_state_file, 0644);
  g_free(txt);
}

static void pb_ctl_remember_styles(PbCtl *self, GstElement *mix) {
  if (self->view_inited)
    return;
  self->view_inited = TRUE;
  static const gchar *names[3] = { "style1", "style2", "style3" };
  for (guint i = 0; i < 3; i++) {
    gchar *txt = NULL;
    if (mix && g_object_class_find_property(G_OBJECT_GET_CLASS(mix), names[i]))
      g_object_get(mix, names[i], &txt, NULL);
    self->style_base[i] = txt ? txt : g_strdup("");
    if (txt && pb_style_is_hidden(txt))
      self->view_hide |= 1 << i;
  }
}

static gint pb_ctl_current_mute(GstElement *vol) {
  gboolean m = FALSE;
  if (vol && g_object_class_find_property(G_OBJECT_GET_CLASS(vol), "mute"))
    g_object_get(vol, "mute", &m, NULL);
  return m ? 1 : 0;
}

static void pb_ctl_apply_view(PbCtl *self, gint hide, gint audio, gint mute) {
  GstObject *par = gst_object_get_parent(GST_OBJECT(self));
  if (!par)
    return;
  GstElement *mix = self->mixer_name && *self->mixer_name ? gst_bin_get_by_name(GST_BIN(par), self->mixer_name) : NULL;
  GstElement *vol = self->volume_name && *self->volume_name ? gst_bin_get_by_name(GST_BIN(par), self->volume_name) : NULL;
  pb_ctl_remember_styles(self, mix);
  static const gchar *names[3] = { "style1", "style2", "style3" };
  if (mix) {
    for (guint i = 0; i < 3; i++) {
      if (!g_object_class_find_property(G_OBJECT_GET_CLASS(mix), names[i]))
        continue;
      const gint want = (hide >> i) & 1, have = (self->view_hide >> i) & 1;
      if (want == have)
        continue;
      gchar *clean = pb_style_strip_op(self->style_base[i]);
      gchar *txt = want ? (*clean ? g_strdup_printf("%s,op=0", clean) : g_strdup("op=0")) : g_strdup(clean);
      g_object_set(mix, names[i], txt, NULL);
      g_free(txt);
      g_free(clean);
      self->view_hide = (self->view_hide & ~(1 << i)) | (want << i);
    }
  }
  GstElement *asel = self->audio_selector && *self->audio_selector ? gst_bin_get_by_name(GST_BIN(par), self->audio_selector) : NULL;
  if (asel && audio >= -1 && audio <= 2 && self->have_v) {
    g_atomic_int_set(&self->audio_pos, audio);
    const gint cam = audio < 0 ? self->last_v[0] : self->last_v[1 + MIN(audio, 2)];
    if (cam >= 0 && cam <= 3)
      g_object_set(asel, "state", (guint) cam, NULL);
  }
  if (asel)
    gst_object_unref(asel);
  if (vol && g_object_class_find_property(G_OBJECT_GET_CLASS(vol), "mute"))
    g_object_set(vol, "mute", mute ? TRUE : FALSE, NULL);
  pb_ctl_publish_view(self, self->view_hide, g_atomic_int_get(&self->audio_pos), pb_ctl_current_mute(vol));
  GST_INFO_OBJECT(self, "Ansicht: ausgeblendet %d, Ton %d, stumm %d", self->view_hide, g_atomic_int_get(&self->audio_pos), mute);
  if (mix)
    gst_object_unref(mix);
  if (vol)
    gst_object_unref(vol);
  gst_object_unref(par);
}

static void pb_ctl_poll_view(PbCtl *self, gboolean first) {
  if (!self->view_file || !*self->view_file)
    return;
  struct stat st;
  if (stat(self->view_file, &st) == 0) {
    gint64 mt = (gint64) st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
    if (mt != self->view_mtime) {
      self->view_mtime = mt;
      int v[3] = { -1, -2, 0 };
      FILE *f = fopen(self->view_file, "r");
      if (f) {
        if (fscanf(f, "%d %d %d", &v[0], &v[1], &v[2]) < 3)
          v[0] = -1;
        fclose(f);
      }
      if (v[0] >= 0 && v[0] <= 7 && v[1] >= -1 && v[1] <= 2 && v[2] >= 0 && v[2] <= 1) {
        pb_ctl_apply_view(self, v[0], v[1], v[2]);
        return;
      }
    }
  }
  if (first) {
    GstObject *par = gst_object_get_parent(GST_OBJECT(self));
    if (par) {
      GstElement *mix = self->mixer_name && *self->mixer_name ? gst_bin_get_by_name(GST_BIN(par), self->mixer_name) : NULL;
      GstElement *vol = self->volume_name && *self->volume_name ? gst_bin_get_by_name(GST_BIN(par), self->volume_name) : NULL;
      pb_ctl_remember_styles(self, mix);
      pb_ctl_publish_view(self, self->view_hide, g_atomic_int_get(&self->audio_pos), pb_ctl_current_mute(vol));
      if (mix)
        gst_object_unref(mix);
      if (vol)
        gst_object_unref(vol);
      gst_object_unref(par);
    }
  }
}

static void pb_ctl_poll_delay(PbCtl *self) {
  struct stat st;
  if (self->file && stat(self->file, &st) == 0) {
    gint64 mt = (gint64) st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
    if (mt != self->last_mtime) {
      self->last_mtime = mt;
      int v[4] = { -1, 0, 0, 0 };
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
}

static void pb_ctl_publish_live(PbCtl *self) {
  if (!self->live_file || !*self->live_file)
    return;
  const gint64 rt = pb_sel_running_time((GstElement *) self);
  if (rt < 0)
    return;
  gchar txt[16];
  for (gint i = 0; i < SEL_PADS; i++) {
    const gint64 v = pb_live_get(i);
    txt[i * 2] = (v >= 0 && (GstClockTime) (rt - v) <= LIVE_DEAD_NS) ? '1' : '0';
    txt[i * 2 + 1] = i == SEL_PADS - 1 ? '\n' : ' ';
  }
  txt[SEL_PADS * 2] = 0;
  if (strcmp(txt, self->live_last) == 0 && rt - self->live_written_rt < 2 * GST_SECOND)
    return;
  gchar *tmp = g_strdup_printf("%s.tmp", self->live_file);
  if (g_file_set_contents(tmp, txt, -1, NULL)) {
    g_chmod(tmp, 0644);
    g_rename(tmp, self->live_file);
    g_strlcpy(self->live_last, txt, sizeof(self->live_last));
    self->live_written_rt = rt;
  }
  g_free(tmp);
}

static gpointer pb_ctl_thread(gpointer data) {
  PbCtl *self = data;
  guint tick = 0;
  while (g_atomic_int_get(&self->run)) {
    pb_ctl_publish_live(self);
    if (tick % 3 == 0)
      pb_ctl_poll_delay(self);
    pb_ctl_poll_select(self, tick == 0);
    pb_ctl_poll_view(self, tick == 0);
    tick++;
    g_usleep(100000);
  }
  return NULL;
}

static GstStateChangeReturn pb_ctl_change_state(GstElement *el, GstStateChange t) {
  PbCtl *self = (PbCtl *) el;
  if (t == GST_STATE_CHANGE_PAUSED_TO_PLAYING && !self->thread) {
    self->last_mtime = 0;
    self->sel_mtime = 0;
    self->view_mtime = 0;
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

static gchar **pb_ctl_str_slot(PbCtl *self, guint id) {
  switch (id) {
    case CTL_FILE: return &self->file;
    case CTL_VQ: return &self->video_queue;
    case CTL_AQ: return &self->audio_queue;
    case CTL_PQ: return &self->pip_queue;
    case CTL_P2Q: return &self->pip2_queue;
    case CTL_P3Q: return &self->pip3_queue;
    case CTL_CAM0: return &self->cam[0];
    case CTL_CAM1: return &self->cam[1];
    case CTL_CAM2: return &self->cam[2];
    case CTL_CAM3: return &self->cam[3];
    case CTL_SEL: return &self->selector;
    case CTL_ASEL: return &self->audio_selector;
    case CTL_SELFILE: return &self->select_file;
    case CTL_STATEFILE: return &self->state_file;
    case CTL_VIEWFILE: return &self->view_file;
    case CTL_VIEWSTATE: return &self->view_state_file;
    case CTL_MIXER: return &self->mixer_name;
    case CTL_VOLUME: return &self->volume_name;
    case CTL_LIVE: return &self->live_file;
    default: return NULL;
  }
}

static void pb_ctl_set_property(GObject *o, guint id, const GValue *v, GParamSpec *ps) {
  PbCtl *self = (PbCtl *) o;
  if (id == CTL_APOS) {
    g_atomic_int_set(&self->audio_pos, g_value_get_int(v));
    return;
  }
  gchar **dst = pb_ctl_str_slot(self, id);
  if (!dst) {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
    return;
  }
  g_free(*dst);
  *dst = g_value_dup_string(v);
}

static void pb_ctl_get_property(GObject *o, guint id, GValue *v, GParamSpec *ps) {
  PbCtl *self = (PbCtl *) o;
  if (id == CTL_APOS) {
    g_value_set_int(v, g_atomic_int_get(&self->audio_pos));
    return;
  }
  gchar **src = pb_ctl_str_slot(self, id);
  if (!src) {
    G_OBJECT_WARN_INVALID_PROPERTY_ID(o, id, ps);
    return;
  }
  g_value_set_string(v, *src);
}

static void pb_ctl_finalize(GObject *o) {
  PbCtl *self = (PbCtl *) o;
  for (guint id = CTL_FILE; id <= CTL_VOLUME; id++) {
    gchar **p = pb_ctl_str_slot(self, id);
    if (p)
      g_free(*p);
  }
  g_free(self->live_file);
  for (guint i = 0; i < 3; i++)
    g_free(self->style_base[i]);
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
  {
    static const struct { guint id; const gchar *name; } cp[] = { {CTL_CAM0, "cam0"}, {CTL_CAM1, "cam1"}, {CTL_CAM2, "cam2"}, {CTL_CAM3, "cam3"} };
    for (guint i = 0; i < G_N_ELEMENTS(cp); i++)
      g_object_class_install_property(oc, cp[i].id,
          g_param_spec_string(cp[i].name, "Warteschlangen der Kamera", "Liste name:art (v Bild groß, s Bild klein, a Ton), die zur Verzögerung dieser Kamera gehören", NULL,
                              G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  }
  g_object_class_install_property(oc, CTL_SEL,
      g_param_spec_string("selector", "Bild-Umschalter", "Name des pbpipsel für das Bild", NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_ASEL,
      g_param_spec_string("audio-selector", "Ton-Umschalter", "Name des pbpipsel für den Ton", NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_SELFILE,
      g_param_spec_string("select-file", "Umschalt-Datei", "Datei mit vier Zahlen: Hauptkamera und Kamera an Stelle 1 bis 3 (15 = keine)", "/var/lib/pipbox/main-select",
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, CTL_STATEFILE,
      g_param_spec_string("state-file", "Zustandsdatei", "Hier meldet pbctl den tatsächlich eingestellten Zustand zurück", "/run/pipbox-send/swap-state",
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, CTL_VIEWFILE,
      g_param_spec_string("view-file", "Ansicht-Datei", "Datei mit drei Zahlen: ausgeblendet (Bit 0 bis 2 = kleines Bild 1 bis 3), Tonquelle (-1 Hauptbild, 0 bis 2), stumm (0/1)",
                          "/var/lib/pipbox/main-view", G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, CTL_VIEWSTATE,
      g_param_spec_string("view-state-file", "Ansicht-Zustandsdatei", "Hier meldet pbctl die tatsächlich gestellte Ansicht zurück", "/run/pipbox-send/view-state",
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, CTL_MIXER,
      g_param_spec_string("mixer", "Mischer", "Name des pbpipmix, dessen Stile style1 bis style3 zum Ein-/Ausblenden gestellt werden", "pipmix",
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, CTL_VOLUME,
      g_param_spec_string("volume", "Lautstärke-Element", "Name des volume-Elements, das stumm geschaltet wird", "avol",
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS | G_PARAM_CONSTRUCT));
  g_object_class_install_property(oc, CTL_APOS,
      g_param_spec_int("audio-pos", "Ton von Stelle", "-1 Ton der Hauptkamera, 0 bis 2 Ton der Kamera an dieser Stelle", -1, 2, -1,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  g_object_class_install_property(oc, CTL_LIVE,
      g_param_spec_string("live-file", "Kamera-Datei", "Hier meldet pbctl, welche Kameras gerade Bilder liefern (vier Zahlen 0/1)", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
  gst_element_class_set_static_metadata(ec, "IRL4YOU PiP Steuerung", "Generic",
      "Stellt Wartezeiten und den Umschalter im laufenden Betrieb um", "IRL4YOU");
  ec->change_state = pb_ctl_change_state;
}

static void pb_ctl_init(PbCtl *self) {
  self->thread = NULL;
  self->run = 0;
  self->last_mtime = 0;
  self->sel_mtime = 0;
  self->view_mtime = 0;
  self->audio_pos = -1;
  self->view_inited = FALSE;
  self->view_hide = 0;
  self->have_v = FALSE;
  self->live_last[0] = 0;
  self->live_written_rt = 0;
}

static gboolean plugin_init(GstPlugin *plugin) {
  GST_DEBUG_CATEGORY_INIT(pbpip_debug, "pbpip", 0, "IRL4YOU PiP");
  return gst_element_register(plugin, "pbpipsink", GST_RANK_NONE, pb_pip_sink_get_type()) &&
         gst_element_register(plugin, "pbpipmix", GST_RANK_NONE, pb_pip_mix_get_type()) &&
         gst_element_register(plugin, "pbpipsel", GST_RANK_NONE, pb_pip_sel_get_type()) &&
         gst_element_register(plugin, "pbtap", GST_RANK_NONE, pb_tap_get_type()) &&
         gst_element_register(plugin, "pbround", GST_RANK_NONE, pb_round_get_type()) &&
         gst_element_register(plugin, "pbctl", GST_RANK_NONE, pb_ctl_get_type());
}

#ifndef PACKAGE
#define PACKAGE "irl4you-pip"
#endif
GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, pbpip,
                  "IRL4YOU Bild-in-Bild: schreibt das kleine Bild in das Hauptbild",
                  plugin_init, "0.1", "MIT/X11", "irl4you-pip", "https://github.com/IRL4YOU/irl4you-pip")
