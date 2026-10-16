/* IRL4YOU PIP: Bild-in-Bild-Baustein für GStreamer (NV12).
 *
 * Zwei Elemente, die sich einen kleinen Zwischenspeicher teilen (Eigenschaft slot 0, 1 oder 2,
 * so sind bis zu drei kleine Bilder gleichzeitig möglich; Platz 3 gehört der Hauptkamera beim Tausch):
 *
 *   pbpipsink  nimmt das KLEINE Bild entgegen (schon vom Hardware-Decoder auf z. B. 480x270
 *              verkleinert) und legt es in einem Ringspeicher ab.
 *   pbpipmix   steht im Hauptbild-Pfad und schreibt pro Hauptbild das nächste kleine Bild an
 *              die gewählte Ecke HINEIN. Das große Bild wird nie gelesen, außer ein Stil (Eigenschaften
 *              style1 bis style3: Beschnitt, Deckkraft, Rahmen, Eckenrundung) verlangt es: dann nur in
 *              genau den Bildpunkten, die vom Hauptbild abhängen (Deckkraft unter 100 %, durchscheinender
 *              Rahmen, weiche Kante an gerundeten Ecken). Ohne Stil bleibt es beim reinen Schreiben.
 *
 * Warum so: Auf dem RK3588 ist das Lesen von Decoder-Bildern durch die CPU extrem langsam
 * (gemessen: 1080p-Umwandlung ca. 9 Bilder/s). Schreiben in diesen Speicher ist schnell.
 * Ein Standard-Mischer (compositor) muss das große Bild lesen und schafft deshalb kein 30 fps.
 *
 * Der Ringspeicher gleicht Schübe des kleinen Bildes aus (Vorlauf von wenigen Bildern, zu alte
 * Bilder werden verworfen). Bleibt das kleine Bild länger als 2 Sekunden aus, wird nichts mehr
 * eingeblendet (statt eines eingefrorenen Bildes).
 *
 * Drittes Element pbctl: liest alle 0,3 s eine kleine Datei (bis zu vier Zahlen in Millisekunden: Hauptbild,
 * kleines Bild 1, 2 und 3) und stellt damit die Wartezeit (min-threshold-time) benannter queue-Elemente um.
 * So lassen sich die Verzögerungen im laufenden Betrieb ändern, ohne die Sendekette neu zu starten.
 *
 * Viertes Element pbpipsel (Tausch ohne Unterbrechung): wählt eines von bis zu vier Eingängen (jede Kamera hat
 * dann zwei Zweige: groß für das Hauptbild, klein für das Bild-in-Bild). Verworfen wird, was nicht gewählt ist;
 * die Zeitstempel laufen beim Umschalten lückenlos weiter (der Encoder sieht keinen Sprung). pbctl schaltet es
 * über eine kleine Datei um, pbpipmix erfährt über das Bild selbst, welche kleinen Bilder jetzt zu zeigen sind.
 *
 * MIT-Lizenz, Copyright (c) 2026 IRL4YOU. Eigene Umsetzung.
 */
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

#define RING_CAP 12              /* höchstens so viele kleine Bilder vorhalten */
#define PREBUFFER 3              /* Vorlauf, bevor das erste Bild gezeigt wird */
#define NSLOTS 4                 /* Plätze 0 bis 2: kleine Bilder 1 bis 3; Platz 3: kleines Bild der Hauptkamera (nur beim Tausch ohne Unterbrechung) */
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
  /* Ein großes Bild (bis so groß wie das Hauptbild) behält keinen vollen Rand: Der Rand schrumpft, das Bild bleibt im Bild */
  if (corner != 5u) {
    if (*x > mw - pw) *x = mw - pw;
    if (*y > mh - ph) *y = mh - ph;
    if (*x < 0) *x = 0;
    if (*y < 0) *y = 0;
  }
}
/* PB_POS_END */

/* PB_DRAW_BEGIN  (wird von tools/test_pbdraw.py zusammen mit PB_POS einzeln übersetzt und geprüft: reine Ganzzahlrechnung ohne GStreamer) */
/* Gestaltung eines kleinen Bildes: Beschnitt, Deckkraft, Rahmen mit Eckenrundung.
 *
 * Das kleine Bild ist NV12, dicht gepackt (Stride = Breite): erst die Y-Ebene, dann die UV-Ebene. Das Hauptbild wird in
 * Y und UV mit je eigenem Stride beschrieben. Ohne Stil (Deckkraft 100 %, kein Rahmen) werden nur Zeilen kopiert und das
 * Hauptbild nie gelesen. Mit Stil wird das Hauptbild nur dort gelesen, wo das Ergebnis davon abhängt: Deckkraft unter
 * 100 % und die weiche Kante an gerundeten Ecken. Ein durchscheinender Rahmen mischt Rahmenfarbe und Bild (beides liegt im
 * Ring-Speicher), das Hauptbild geht erst über die Deckkraft des ganzen Bildes ein.
 *
 * Schichtung je Bildpunkt: S = Bild + (Rahmenfarbe - Bild) * Rahmenanteil; Ergebnis = Hauptbild + (S - Hauptbild) * Deckkraft.
 * Rahmenanteil = bo * (1 - Abdeckung der Innenform), Deckkraft = op * Abdeckung der Außenform. Beides wird in Festkomma mit
 * PB_ONE = 1024 gerechnet (ein Rundungsschritt am Ende, bei Deckkraft 1 geht das Hauptbild gar nicht ein).
 *
 * Chroma (UV) liegt auf einem 2x2-Raster: Rahmenanteil und Deckkraft eines UV-Paares sind das Mittel der vier zugehörigen
 * Luma-Bildpunkte. Das Hauptbild-Chroma wird nur gelesen, wenn dieses Mittel unter voller Deckkraft liegt. */
#define PB_SHIFT 10
#define PB_ONE (1 << PB_SHIFT)                /* Abdeckung/Anteil in Festkomma: PB_ONE = voll */
#define PB_HALF (1 << (2 * PB_SHIFT - 1))     /* halbe Einheit nach der Multiplikation zweier Festkomma-Werte */
#define PB_CHUNK 128                          /* Bildpunkte je Arbeitsstück (Puffer liegen auf dem Stack) */

typedef struct {
  gint op;                 /* Deckkraft des ganzen Bildes in Prozent (0 bis 100); 0 = nicht zeichnen */
  gint cl, cr, ct, cb;     /* Beschnitt links/rechts/oben/unten in Pixeln eines 1920x1080-Bildes (0 bis 1900) */
  gint bw;                 /* Rahmenbreite in Pixeln eines 1920 Pixel breiten Hauptbildes (0 bis 64); 0 = kein Rahmen */
  guint bc;                /* Rahmenfarbe 0xRRGGBB */
  gint bo;                 /* Deckkraft des Rahmens über dem Bild in Prozent (0 bis 100) */
  gint br;                 /* Eckenradius in Pixeln eines 1920 Pixel breiten Hauptbildes (0 bis 200); gilt für das Bild selbst, mit oder ohne Rahmen */
} PbStyle;

/* Ort und Größe des (beschnittenen) kleinen Bildes: Ausschnitt (cx, cy, cw, ch) im kleinen Bild, Ziel (x, y) im Hauptbild */
typedef struct { gint cx, cy, cw, ch, x, y; } PbPlace;

static void pb_style_default(PbStyle *st) {
  st->op = 100;
  st->cl = st->cr = st->ct = st->cb = 0;
  st->bw = 0;
  st->bc = 0xffffffu;
  st->bo = 100;
  st->br = 0;
}

/* Kleine Helfer für den Text [s, e): ohne Bibliotheksfunktionen, damit der Abschnitt einzeln übersetzbar bleibt */
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

/* Ganze Zahl mit optionalem Vorzeichen. Sehr große Zahlen bleiben sehr groß (werden danach begrenzt), ohne zu überlaufen. */
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

/* Stil aus Text "schluessel=wert,..." lesen. Alle Schlüssel sind optional. Unbekannte Schlüssel, Teile ohne "=" und
 * fehlerhafte Werte werden ignoriert (der Schlüssel behält dann seinen bisherigen Wert), zu große oder zu kleine Zahlen
 * werden begrenzt. Bei doppeltem Schlüssel gilt der letzte. NULL oder leerer Text ergibt den Standardstil. */
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

/* Beschnitt auf ein kleines Bild der Größe sw x sh umrechnen. Die Werte beziehen sich auf ein Bild von 1920x1080 und werden
 * auf die tatsächliche Größe umgerechnet (gerundet) und auf gerade Werte abgerundet (NV12-Chroma ist 2x2). Bleiben weniger
 * als 16x16 Bildpunkte übrig, wird der Beschnitt ganz ignoriert. Rückgabe TRUE: es gilt ein Ausschnitt (cx, cy, cw, ch),
 * FALSE: das ganze Bild (0, 0, sw, sh). Ein Ausschnitt behält seinen Maßstab, er wird nicht vergrößert. */
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

/* Ausschnitt und Platz im Hauptbild (mw x mh) bestimmen. Die Position (Ecke, fx/fy, Rand) wird mit der Größe des
 * Ausschnitts berechnet, nicht mit der des ganzen kleinen Bildes. Rückgabe FALSE: nichts zeichnen (Deckkraft 0, passt
 * nicht ins Hauptbild). x und y sind danach gerade (UV-Raster des Hauptbildes). */
static gboolean pb_place(const PbStyle *st, gint mw, gint mh, gint pw, gint ph, guint corner, gint fx, gint fy, PbPlace *pl) {
  if (st->op <= 0)
    return FALSE;
  pb_style_crop(st, pw, ph, &pl->cx, &pl->cy, &pl->cw, &pl->ch);
  if (pl->cw > mw || pl->ch > mh)
    return FALSE;
  const gint margin = (mw / 60) & ~1;              /* ca. 2 % der Breite, gerade */
  pb_pos(corner, mw, mh, pl->cw, pl->ch, margin, fx, fy, &pl->x, &pl->y);
  pl->x &= ~1;
  pl->y &= ~1;
  return pl->x >= 0 && pl->y >= 0 && pl->x + pl->cw <= mw && pl->y + pl->ch <= mh;
}

/* RGB (0xRRGGBB) nach NV12: BT.709, begrenzter Bereich (Y 16..235, U/V 16..240), gerundet. Koeffizienten in Zehntausendsteln. */
static void pb_rgb_to_nv12(guint rgb, gint *y, gint *u, gint *v) {
  const gint r = (gint) ((rgb >> 16) & 0xffu), g = (gint) ((rgb >> 8) & 0xffu), b = (gint) (rgb & 0xffu);
  const gint den = 255 * 10000;
  *y = 16 + (219 * (2126 * r + 7152 * g + 722 * b) + den / 2) / den;
  *u = (128 * den + 224 * (-1146 * r - 3854 * g + 5000 * b) + den / 2) / den;     /* Zähler bleibt positiv */
  *v = (128 * den + 224 * (5000 * r - 4542 * g - 458 * b) + den / 2) / den;
  *y = PB_CLAMP(*y, 16, 235);
  *u = PB_CLAMP(*u, 16, 240);
  *v = PB_CLAMP(*v, 16, 240);
}

/* Form des gestalteten Bildes (Maße in Bildpunkten des Hauptbildes) */
typedef struct {
  gint cw, ch;             /* Größe des Ausschnitts (gerade) */
  gint bw;                 /* Rahmenbreite, 0 = kein Rahmen */
  gint ro, ri;             /* Eckenradius außen / innen (innen = max(außen - Rahmenbreite, 0)) */
  gint op, bo;             /* Deckkraft Bild / Rahmen in Festkomma 0..PB_ONE */
} PbShape;

/* Rahmenbreite und Eckenradius skalieren mit der Breite des Hauptbildes (Bezug 1920). Der Rahmen liegt innen im Ausschnitt;
 * Breite höchstens min(cw, ch) / 2, Radius ebenso. Der Radius gilt für das Bild selbst, auch ohne Rahmen (dann sind nur die Ecken des Bildes
 * abgerundet); mit Rahmen folgt die Innenkante des Rahmens der Rundung (Radius außen minus Rahmenbreite). */
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

/* Ganzzahlige Quadratwurzel (abgerundet) */
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

/* Abdeckung (0..PB_ONE) des Bildpunkts (fx, fy) im Eckquadrat einer Rundung mit Radius r: fx, fy zählen vom Rand nach innen,
 * der Eckmittelpunkt liegt bei (r, r). Abdeckung = r - Abstand(Bildpunktmitte, Eckmittelpunkt) + 0,5, begrenzt auf 0..1:
 * ein Bildpunkt Übergang. Mit doppelten Abständen gerechnet, damit die Entscheidung "ganz drin / ganz draußen" ohne Wurzel
 * auskommt; nur im Übergang wird die Wurzel gezogen. */
static gint pb_corner_cov(gint fx, gint fy, gint r) {
  const gint64 ux = 2 * (gint64) (r - fx) - 1, uy = 2 * (gint64) (r - fy) - 1;
  const gint64 d2 = ux * ux + uy * uy;               /* (2 * Abstand)^2 */
  const gint64 in = 2 * (gint64) r - 1, out = 2 * (gint64) r + 1;
  if (d2 <= in * in)
    return PB_ONE;
  if (d2 >= out * out)
    return 0;
  const gint64 s = pb_isqrt(d2 << (2 * PB_SHIFT));   /* 2 * Abstand * PB_ONE */
  const gint64 c = (out * PB_ONE - s + 1) / 2;       /* (2r + 1 - 2 * Abstand) / 2 */
  return (gint) PB_CLAMP(c, 0, PB_ONE);
}

/* Rahmenanteil *wb und Deckkraft *a (beide 0..PB_ONE) des Luma-Bildpunkts (x, y) des Ausschnitts. Die Form ist an beiden
 * Achsen symmetrisch; gerechnet wird deshalb mit dem Abstand zum nächsten Rand (fx, fy). */
static void pb_shape_px(const PbShape *s, gint x, gint y, gint *wb, gint *a) {
  const gint fx = MIN(x, s->cw - 1 - x), fy = MIN(y, s->ch - 1 - y);
  gint ao = PB_ONE, ai = PB_ONE;
  if (s->ro > 0 && fx < s->ro && fy < s->ro)
    ao = pb_corner_cov(fx, fy, s->ro);               /* Außenform: Bildpunkte außerhalb bleiben Hauptbild */
  if (s->bw > 0) {
    const gint ix = fx - s->bw, iy = fy - s->bw;     /* Lage zur Innenform (Inset = Rahmenbreite) */
    if (ix < 0 || iy < 0)
      ai = 0;
    else if (s->ri > 0 && ix < s->ri && iy < s->ri)
      ai = pb_corner_cov(ix, iy, s->ri);
  }
  *a = (s->op * ao + PB_ONE / 2) >> PB_SHIFT;
  *wb = (s->bo * (PB_ONE - ai) + PB_ONE / 2) >> PB_SHIFT;
}

/* Für die Zeile y: Breite *zone der Randbereiche (links und rechts), in denen der Bildpunkt einzeln bewertet werden muss.
 * Dazwischen sind Rahmenanteil *wb und Deckkraft *a für die ganze Zeile gleich (Rahmenzeile oder Bildzeile). */
static gint pb_row_zone(const PbShape *s, gint y, gint *wb, gint *a) {
  const gint fy = MIN(y, s->ch - 1 - y);
  gint zone = 0;
  *a = s->op;
  *wb = 0;
  if (s->bw <= 0) {                                  /* ohne Rahmen schneidet nur die Außenrundung an den Ecken */
    if (fy < s->ro)
      zone = s->ro;
    return zone;
  }
  if (fy < s->bw) {                                  /* Zeile liegt ganz im Rahmen, nur die Außenrundung schneidet an den Enden */
    *wb = s->bo;
    if (fy < s->ro)
      zone = s->ro;
  } else {                                           /* Bildzeile: links/rechts Rahmenspalten, an den Rundungen mehr */
    zone = s->bw;
    if (fy < s->ro)
      zone = MAX(zone, s->ro);
    if (s->ri > 0 && fy - s->bw < s->ri)
      zone = MAX(zone, s->bw + s->ri);
  }
  return zone;
}

/* Ein Bildpunkt: Ergebnis aus Hauptbild d, Bild p, Rahmenfarbe b, Rahmenanteil wb und Deckkraft a (0 < a <= PB_ONE).
 * Bei a == PB_ONE geht d nicht ein. Ein einziger Rundungsschritt. */
static gint pb_mix(gint d, gint p, gint b, gint wb, gint a) {
  return (d * (PB_ONE - a) * PB_ONE + (p * (PB_ONE - wb) + b * wb) * a + PB_HALF) >> (2 * PB_SHIFT);
}

/* n (höchstens PB_CHUNK) Luma-Bildpunkte mischen. wbv[i]/av[i]: Rahmenanteil/Deckkraft je Bildpunkt; sind die Zeiger NULL,
 * gelten wc/ac für alle. Bildpunkte mit Deckkraft 0 (außerhalb der Form) werden weder gelesen noch geschrieben. Das Hauptbild
 * wird nur für zusammenhängende Läufe gelesen, die einen Bildpunkt mit Deckkraft unter 1 enthalten, und dann in einem Stück
 * (breite Zugriffe, das ist bei Decoder-Speicher viel schneller als einzelne Bytes). */
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

/* Dasselbe für n UV-Paare (je U und V, 2 Bytes) */
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

/* Lauf mit gleichem Rahmenanteil und gleicher Deckkraft (Mittelteil einer Zeile). Schnellwege: Bild unverändert bei voller
 * Deckkraft (nur kopieren), reine Rahmenfarbe bei voller Deckkraft (nur schreiben). */
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

/* Luma-Bildpunkte x0 bis x1-1 der Zeile y einzeln bewerten und mischen (d, p: Zeilenanfang im Ziel / im Bild) */
static void pb_y_edge(const PbShape *s, gint y, gint x0, gint x1, guint8 *d, const guint8 *p, gint b) {
  gint wbv[PB_CHUNK], av[PB_CHUNK];
  for (gint x = x0; x < x1; x += PB_CHUNK) {
    const gint n = MIN(PB_CHUNK, x1 - x);
    for (gint i = 0; i < n; i++)
      pb_shape_px(s, x + i, y, &wbv[i], &av[i]);
    pb_y_vec(d + x, p + x, n, b, wbv, av, 0, 0);
  }
}

/* UV-Paare k0 bis k1-1 der Paarzeile mit der Luma-Zeile y (gerade) einzeln bewerten: Mittel der vier Luma-Bildpunkte */
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

/* Schreibt den Ausschnitt pl (cx, cy, cw, ch) des kleinen Bildes src (pw x ph, NV12 dicht gepackt) mit Stil st an (x, y) in
 * das Hauptbild: dy/ystride = Y-Ebene, duv/uvstride = UV-Ebene, mw = Breite des Hauptbildes (für Rahmenbreite und Radius).
 * Voraussetzung (stellt pb_place sicher): x, y, cx, cy gerade, der Ausschnitt liegt im kleinen Bild und das Ziel im Hauptbild.
 * Ohne Stil (Deckkraft 100 %, kein Rahmen) werden nur Zeilen kopiert, das Hauptbild wird nicht gelesen. */
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
  const gint np = s.cw / 2;                          /* UV-Paare je Zeile */
  for (gint m = 0; m < s.ch / 2; m++) {
    gint wb0, a0, wb1, a1;
    const gint z0 = pb_row_zone(&s, 2 * m, &wb0, &a0), z1 = pb_row_zone(&s, 2 * m + 1, &wb1, &a1);
    const gint zone = MAX(z0, z1);                   /* beide Zeilen des Paares sind im Mittelteil konstant */
    guint8 *d = duv + (gsize) (y / 2 + m) * uvstride + x;
    const guint8 *p = suv + (gsize) (cy / 2 + m) * pw + cx;
    const gint kz = (zone + 1) / 2, le = MIN(kz, np), rs = MAX(np - kz, le);
    pb_uv_edge(&s, 2 * m, 0, le, d, p, ub, vb);
    pb_uv_run(d + 2 * le, p + 2 * le, rs - le, ub, vb, (wb0 + wb1 + 1) >> 1, (a0 + a1 + 1) >> 1);
    pb_uv_edge(&s, 2 * m, rs, np, d, p, ub, vb);
  }
}
/* PB_DRAW_END */

typedef struct { GstVideoFilter parent; guint corner; guint slot; gint slot2; guint corner2; gint slot3; guint corner3; guint fx, fy, fx2, fy2, fx3, fy3; gint follow_tag; PbStyle style[3]; gchar *style_txt[3]; } PbPipMix;
typedef struct { GstVideoFilterClass parent_class; } PbPipMixClass;
G_DEFINE_TYPE(PbPipMix, pb_pip_mix, GST_TYPE_VIDEO_FILTER)

enum { PROP_0, PROP_CORNER, PROP_WIDTH_PCT, PROP_SLOT, PROP_SLOT2, PROP_CORNER2, PROP_SLOT3, PROP_CORNER3, PROP_X, PROP_Y, PROP_X2, PROP_Y2, PROP_X3, PROP_Y3, PROP_FOLLOW,
       PROP_STYLE1, PROP_STYLE2, PROP_STYLE3 };      /* Stil 1 bis 3 müssen lückenlos aufeinander folgen */

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
    case PROP_FOLLOW: g_atomic_int_set(&self->follow_tag, g_value_get_boolean(v) ? 1 : 0); break;
    case PROP_STYLE1: case PROP_STYLE2: case PROP_STYLE3: {
      /* Text parsen und übernehmen unter ring_lock: der Videofaden liest den Stil unter demselben Schloss und sieht nie etwas Halbes */
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

/* Zeichnet das kleine Bild aus Platz "slot" mit dem Stil st in das Hauptbild. Aufrufer hält ring_lock (schützt auch st). */
static void pb_mix_draw(GstVideoFrame *frame, guint slot, guint corner, gint fx, gint fy, const PbStyle *st) {
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
  /* Ausschnitt, Platz und Stil: Position mit der Größe des beschnittenen Bildes. Deckkraft 0 oder zu groß: nichts zeichnen
   * (der Ring lief oben trotzdem weiter, das Bild erscheint sofort wieder). Ohne Stil bleibt es beim reinen Kopieren. */
  PbPlace pl = { 0, 0, 0, 0, 0, 0 };
  if (draw && pb_place(st, mw, mh, pw, ph, corner, fx, fy, &pl)) {
    guint8 *dy = GST_VIDEO_FRAME_PLANE_DATA(frame, 0), *duv = GST_VIDEO_FRAME_PLANE_DATA(frame, 1);
    const gint sy = GST_VIDEO_FRAME_PLANE_STRIDE(frame, 0), suv = GST_VIDEO_FRAME_PLANE_STRIDE(frame, 1);
    pb_draw_picture(dy, sy, duv, suv, mw, ring->cur, pw, ph, &pl, st);
  }
}

/* Ein wartendes kleines Bild (gerade nicht gezeigt) auf den Vorlauf beschneiden: beim Tausch passt es dann sofort und
 * mit demselben Abstand zum Hauptbild wie im Dauerbetrieb. Aufrufer hält ring_lock. */
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
  /* Tausch ohne Unterbrechung: pbpipsel schreibt in GST_BUFFER_OFFSET, welche Kamera Hauptbild ist und welche Kamera
   * an welcher Stelle (1 bis 3) kleiner gezeigt wird. Der Zustand reist so mit dem Bild und passt immer genau zu ihm. */
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
        continue;                                      /* 15: an dieser Stelle gibt es kein kleines Bild */
      const guint ring = (cam + 3) % NSLOTS;           /* Kamera 1 -> Platz 0, 2 -> 1, 3 -> 2, Hauptkamera 0 -> 3 */
      pb_mix_draw(frame, ring, cn[p], fx[p], fy[p], &self->style[p]);   /* Stil p+1 gehört zur Stelle p, egal welche Kamera dort ist */
      shown |= 1u << ring;
    }
    for (guint r = 0; r < NSLOTS; r++)
      if (!(shown & (1u << r)))
        pb_mix_trim(r);
  } else {
    pb_mix_draw(frame, self->slot, g_atomic_int_get(&self->corner), g_atomic_int_get(&self->fx), g_atomic_int_get(&self->fy), &self->style[0]);
    if (self->slot2 >= 0)                              /* zweites kleines Bild im selben Durchgang (nur ein Mapping des Hauptbilds) */
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
    /* style1 gilt für das Bild von slot/corner/x/y, style2 für slot2/corner2/x2/y2, style3 für slot3/corner3/x3/y3; im Modus
     * follow-tag gilt style<p+1> für die Stelle p, egal welche Kamera dort gerade ist */
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

/* ------------------------------------------------------------------ pbpipsel */

#define SEL_PADS 4
#define STATE_MARK G_GUINT64_CONSTANT(0x9000000000000000)

/* Wählt eines von bis zu vier Eingängen (Anfragen-Pads sink_0 bis sink_3, Nummer = Kamera). Alles, was nicht vom gewählten
 * Eingang kommt, wird sofort verworfen. Die Eigenschaft "state" ist eine Zahl: untere 4 Bit = gewählter Eingang, die
 * nächsten drei Viertelbytes = Kamera an Stelle 1 bis 3 des Bild-in-Bilds (15 = keine). Mit tag-offset schreibt das Element
 * den Zustand in GST_BUFFER_OFFSET jedes Bildes (Markierung 9 in den obersten 4 Bit), damit pbpipmix ihn zum Bild bekommt.
 *
 * Zeitstempel: beim Umschalten wird der Aufschlag so gewählt, dass das erste Bild des neuen Eingangs genau dort anschließt,
 * wo das letzte des alten endete. Segment und Datenstrom-Anfang sendet das Element selbst (einmal); die der Eingänge
 * werden verworfen. So sieht alles dahinter (Encoder, Muxer) einen einzigen, ununterbrochenen Strom. */
typedef struct {
  GstElement parent;
  GstPad *src;
  GstPad *sinks[SEL_PADS];
  GstCaps *caps[SEL_PADS];
  GMutex lock;
  guint state;                   /* atomar lesen/schreiben */
  gboolean tag_offset, force_key;
  gint sent_pad;                 /* Eingang, der zuletzt Daten durchgelassen hat (-1: noch keiner) */
  GstCaps *sent_caps;
  gboolean sent_start, sent_seg, resync;
  gint64 offset;                 /* wird auf die Zeitstempel des gewählten Eingangs aufgeschlagen */
  gboolean have_next;
  GstClockTime next_out, prev_pts;
  guint64 switches;
  GMutex plock;                  /* schützt p_valid, p_avg, p_rt (jeder Eingang schreibt seine, beim Umschalten liest ein anderer) */
  gboolean p_valid[SEL_PADS];
  gint64 p_avg[SEL_PADS];        /* je Eingang: gleitender Mittelwert von (Zeitstempel - Laufzeit der Pipeline) = der "typische" Abstand */
  gint64 p_rt[SEL_PADS];         /* Laufzeit der letzten Probe (für den Abfall) */
  gint64 dropped_ns;             /* beim Umschalten verworfene Dauer des neuen Eingangs (Ton) */
  gint drop_idx;                 /* für welchen Eingang dropped_ns gilt */
  gboolean first_after_switch;   /* der nächste Puffer ist der erste nach dem Umschalten (Ton) */
  gboolean fade_in;              /* der nächste Puffer bekommt eine kurze Einblendung (Ton, nach dem Umschalten) */
  gboolean c_valid;              /* Ton: c_ref gültig */
  gint64 c_ref;                  /* Ton: fester Bezug (Aufschlag + p_avg des Eingangs): die Ausgangszeit liegt auf Laufzeit + c_ref (Echtzeit) */
  gint pend_idx;                 /* Ton: Eingang, auf den gerade gewartet wird (-1: keiner) */
  gint64 pend_since;             /* Laufzeit, seit der auf pend_idx gewartet wird */
  GstBuffer *held;               /* Ton: der letzte Puffer wird einen Schritt zurückgehalten, damit er beim Umschalten ausgeblendet werden kann */
  /* Ausgang am Leben halten ("immer bereit"): liefert der gewählte Eingang nichts, schickt ein Faden schwarze Bilder / Stille im Takt */
  GstCaps *fill_caps;            /* Format, bevor ein Eingang eines geliefert hat (leer: kein Füllen) */
  GThread *fill_thread;
  volatile gint fill_run;
  gint64 last_push_rt;           /* Laufzeit, zu der zuletzt ECHTE Daten weitergegeben wurden (-1: noch keine) */
  gboolean after_fill;           /* zuletzt wurde gefüllt: der nächste echte Puffer darf nicht vor das Ende des Gefüllten */
  gboolean filling;              /* es wird gerade gefüllt */
  gint64 fill_rt0;               /* Ton: Laufzeit und Ausgangszeit bei Beginn des Füllens (die Stille läuft in Echtzeit) */
  GstClockTime fill_out0;
} PbPipSel;
typedef struct { GstElementClass parent_class; } PbPipSelClass;
G_DEFINE_TYPE(PbPipSel, pb_pip_sel, GST_TYPE_ELEMENT)

enum { SEL_0, SEL_STATE, SEL_TAG, SEL_KEY, SEL_FILL };

/* Wann zuletzt ein Bild je Kamera (Nummer des Eingangs) am Bild-Umschalter ankam, in Laufzeit der Pipeline (-1: noch keins). Der Ton-Umschalter
 * liest es, um zu wissen, ob die Kamera da ist (der DJI-Ton kommt stoßweise, darum zählt das Bild); pbctl meldet es der Oberfläche. Es gibt
 * höchstens eine Sendekette je Prozess. */
static GMutex pb_live_lock;
static gint64 pb_live_rt[SEL_PADS] = { -1, -1, -1, -1 };
#define LIVE_DEAD_NS (1500 * GST_MSECOND)       /* so lange ohne Bild gilt eine Kamera als nicht da (Anzeige und Wechsel der Hauptkamera) */
#define FILL_VIDEO_AFTER (500 * GST_MSECOND)    /* Bild: so lange ohne Bild vom gewählten Eingang, dann schwarze Bilder */
#define FILL_AUDIO_AFTER (150 * GST_MSECOND)    /* Ton: so lange ohne Ton, wenn das Bild der Kamera fehlt, dann Stille */
#define FILL_DEAD (700 * GST_MSECOND)           /* Ton: so lange fehlt das Bild einer Kamera, dann gilt ihr Ton als weg */
#define FILL_AUDIO_LONG (3 * GST_SECOND)        /* Ton: so lange ohne Ton, auch wenn das Bild da ist (Kamera ohne Mikrofon), dann Stille */

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

/* Datenstrom-Anfang, Format und Segment vor dem ersten Puffer (und bei geändertem Format) nach hinten schicken. Aufrufer hält lock. */
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

/* Laufzeit der Pipeline (Uhr minus Basiszeit), -1 solange es keine Uhr gibt. */
static gint64 pb_sel_running_time(GstElement *el) {
  GstClock *c = gst_element_get_clock(el);
  if (!c)
    return -1;
  const gint64 r = (gint64) gst_clock_get_time(c) - (gint64) gst_element_get_base_time(el);
  gst_object_unref(c);
  return r;
}

#define SEL_P_TAU (10 * GST_SECOND)      /* Zeitkonstante des gleitenden Mittelwerts von (Zeitstempel - Laufzeit) je Eingang */
#define SEL_MAX_AHEAD (5 * GST_SECOND)   /* weiter als so viel darf der neue Eingang nicht vor dem Ende des alten liegen (sonst nahtlos anschließen) */
#define SEL_MAX_DROP (3 * GST_SECOND)    /* so viel vom neuen Eingang wird höchstens verworfen, bis er passt (sonst nahtlos anschließen) */
#define SEL_TOLERANCE (400 * GST_MSECOND) /* Ton: so weit darf der neue Eingang beim Umschalten von der Echtzeit abweichen (Versatz zum Bild) */
#define SEL_HOLE (50 * GST_MSECOND)       /* Ton: so weit darf der neue Eingang beim Umschalten nach dem Ende des alten liegen (sonst wird gewartet) */
#define SEL_MAX_WAIT (2500 * GST_MSECOND) /* Ton: so lange wartet der Umschalter auf einen passenden Puffer des neuen Eingangs (der alte spielt weiter) */

/* Je Eingang den "typischen" Abstand Zeitstempel zu Laufzeit nachführen (auch für nicht gewählte Eingänge). Puffer kommen stoßweise (die
 * Warteschlange davor gibt gestaut frei): der späteste Puffer eines Stoßes ist der typische, deshalb der höchste Wert, langsam abfallend. */
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
    /* gleitender Mittelwert (Zeitkonstante SEL_P_TAU): der "typische" Abstand. Der höchste Wert (früheste Ankunft) war ein Ausreißer nach oben und
     * machte den Eingang mit stärker stoßweisem Ton dauerhaft um bis zu 1,8 s "zu spät" (gemessen, Stille beim Wechsel). */
    const gint64 dt = rt > self->p_rt[idx] ? rt - self->p_rt[idx] : 0;
    gdouble alpha = (gdouble) dt / (gdouble) SEL_P_TAU;
    if (alpha > 0.2)
      alpha = 0.2;
    self->p_avg[idx] += (gint64) ((gdouble) (sample - self->p_avg[idx]) * alpha);
  }
  self->p_rt[idx] = rt;
  g_mutex_unlock(&self->plock);
}

/* Ton: Format des Eingangs, wenn es 16-Bit-Ganzzahlen verschachtelt sind (so macht es die Pipeline vor dem Umschalter). Sonst keine Blenden/Stille. */
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

/* Lineare Blende über den ganzen Puffer (Ein- oder Ausblendung), Puffer wird beschreibbar gemacht. */
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

#define SEL_SILENCE_MAX (12 * GST_SECOND)    /* so viel Stille wird beim Umschalten höchstens eingefügt (steht der alte Eingang, ist das die Zeit, die er fehlte) */
#define SEL_SILENCE_MIN (5 * GST_MSECOND)    /* kürzere Lücken bleiben (Zeitstempel schließen dort einfach an) */

/* Beim Umschalten (Aufrufer hält lock): den zurückgehaltenen letzten Puffer des alten Eingangs ausblenden und weitergeben, danach Stille von
 * next_out bis to einfügen. Der Ausgang bleibt so ohne Lücke (der Muxer wartet sonst auf den Ton und hält das Bild zurück, gemessen: 1,6 s
 * Bildstillstand je Wechsel) und der Wechsel knackt nicht. */
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

/* ---- Füllen ("immer bereit"): Liefert der gewählte Eingang nichts (Kamera weg, noch nicht da), hält ein Faden den Ausgang am Leben, damit Encoder
 * und Muxer nie auf einen Eingang warten und die Sendung nicht neu startet. Bild: schwarze Bilder im Takt der Bildrate; Ton: Stille in Echtzeit.
 * Kommt der Eingang zurück, schließen die echten Puffer ohne Rückwärtssprung an (after_fill). Nur mit der Eigenschaft fill-caps. */

/* Datenstrom-Anfang, Format und Segment für das Füllen (Aufrufer hält lock). caps: das zu sendende Format. */
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
  if (self->last_push_rt < 0 && !self->filling) {                    /* die Frist beginnt erst, wenn die Sendekette läuft */
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
  const gint64 target = rt + self->offset;                           /* dahin gehörten die echten Bilder jetzt */
  for (guint n = 0; n < 30 && (gint64) self->next_out <= target; n++) {
    GstBuffer *b = gst_buffer_new_and_alloc(GST_VIDEO_INFO_SIZE(&vi));
    GstMapInfo m;
    if (!gst_buffer_map(b, &m, GST_MAP_WRITE)) {
      gst_buffer_unref(b);
      break;
    }
    const gsize y_off = GST_VIDEO_INFO_PLANE_OFFSET(&vi, 0), uv_off = GST_VIDEO_INFO_PLANE_OFFSET(&vi, 1);
    memset(m.data + y_off, 16, uv_off - y_off);                      /* schwarz (Y 16, U/V 128) */
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
  const gboolean cam_dead = vl >= 0 && (GstClockTime) (rt - vl) > FILL_DEAD;       /* das Bild der Kamera fehlt: ihr Ton ist weg */
  const GstClockTime quiet = (GstClockTime) (rt - self->last_push_rt);
  if (!self->filling && !((cam_dead && quiet >= FILL_AUDIO_AFTER) || quiet >= FILL_AUDIO_LONG)) {
    g_mutex_unlock(&self->lock);
    return;
  }
  if (!pb_sel_push_sticky_fill_locked(self, caps)) {
    g_mutex_unlock(&self->lock);
    return;
  }
  if (!self->filling) {                                              /* Beginn: ab dem Ende des letzten Puffers in Echtzeit weiter */
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
  if (self->tag_offset) {                                          /* Bild: vermerken, dass diese Kamera da ist (auch wenn sie nicht gewählt ist) */
    const gint64 rt0 = pb_sel_running_time((GstElement *) self);
    if (rt0 >= 0)
      pb_live_touch(idx, rt0);
  }
  if (!self->tag_offset)                                           /* Ton: je Eingang den typischen Zeitabstand führen (Bild nicht) */
    pb_sel_sample(self, idx, GST_BUFFER_PTS(buf));
  /* Ton: Wurde ein anderer Eingang gewählt, spielt der bisherige weiter, bis ein passender Puffer des neuen kommt (kein Loch, keine Stille). */
  const gboolean old_playing = !self->tag_offset && g_atomic_int_get(&self->sent_pad) == idx;
  if ((gint) (g_atomic_int_get(&self->state) & 0xF) != idx && !old_playing) {      /* nicht gewählt: sofort verwerfen, ohne zu warten */
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
  if (target == self->sent_pad)                                      /* zurückgewechselt oder kein Wechsel: es wird auf nichts gewartet */
    self->pend_idx = -1;
  if (self->sent_pad != idx) {                                       /* Umschalten (oder erster Puffer) */
    if (!pb_sel_push_sticky_locked(self, idx)) {
      g_mutex_unlock(&self->lock);
      gst_buffer_unref(buf);
      return GST_FLOW_NOT_NEGOTIATED;
    }
    const GstClockTime pts = GST_BUFFER_PTS(buf);
    if (self->sent_pad >= 0) {
      gint64 offset = self->have_next && GST_CLOCK_TIME_IS_VALID(pts) ? (gint64) self->next_out - (gint64) pts : self->offset;   /* nahtlos an das Ende des alten Eingangs */
      if (self->have_next && GST_CLOCK_TIME_IS_VALID(pts) && audio_fx) {
        /* Ton: Der DJI-Ton kommt stoßweise (Stücke von bis zu 1,7 s); zwischen dem letzten Stück des alten und dem ersten des neuen Eingangs
         * klafft sonst eine Lücke (gemessen 0,9 bis 1,7 s Stille je Wechsel). Darum spielt der alte Eingang weiter, bis ein Puffer des neuen
         * "passt": seine Echtzeit-Lage (Aufschlag aus dem festen Bezug c_ref und p_avg des neuen Eingangs) weicht höchstens SEL_TOLERANCE vom Ende
         * des alten ab. Dann wird nahtlos angeschlossen (Zeit läuft nie rückwärts, es gibt kein Loch), der Versatz zum Bild bleibt unter der
         * Toleranz und summiert sich nicht auf, weil der Bezug fest ist. Kommt in SEL_MAX_WAIT keiner, wird trotzdem angeschlossen. */
        const gint64 rt = pb_sel_running_time((GstElement *) self);
        g_mutex_lock(&self->plock);
        const gboolean ok = self->p_valid[self->sent_pad] && self->p_valid[idx];
        const gint64 p_old = self->p_avg[self->sent_pad], p_new = self->p_avg[idx];
        g_mutex_unlock(&self->plock);
        if (ok && rt >= 0) {
          if (!self->c_valid) {                                       /* fester Bezug beim ersten Wechsel: so liegt der laufende Ton jetzt */
            self->c_ref = self->offset + p_old;
            self->c_valid = TRUE;
          }
          if (self->pend_idx != idx) {
            self->pend_idx = idx;
            self->pend_since = rt;
          }
          const gint64 out_ideal = (gint64) pts + self->c_ref - p_new;
          const gint64 diff = out_ideal - (gint64) self->next_out;      /* > 0: neuer liegt später als das Ende des alten */
          const gboolean waited_long = rt - self->pend_since > (gint64) SEL_MAX_WAIT;
          /* Passend: der neue Puffer liegt höchstens SEL_TOLERANCE vor und höchstens SEL_HOLE nach dem Ende des alten. Zu spät wäre die Zeit des Tons
           * hinter der Echtzeit (gemessen: schon 0,4 s lassen den Muxer das Bild stoßweise freigeben, bis zum Stillstand); zu früh (der neue Puffer ist
           * verspätet ausgeliefert) ist unkritisch, der Ton läuft dann bis zur Toleranz später als das Bild. */
          if ((diff > (gint64) SEL_HOLE || diff < -(gint64) SEL_TOLERANCE) && !waited_long) {
            g_mutex_unlock(&self->lock);                                /* noch nicht passend: verwerfen, der alte spielt weiter */
            gst_buffer_unref(buf);
            return GST_FLOW_OK;
          }
          if (waited_long) {
            if (diff > (gint64) SEL_HOLE)                               /* nichts Passendes gekommen, neuer liegt später: auf die Echtzeit-Lage springen, Lücke mit Stille füllen */
              offset = self->c_ref - p_new;
            else
              self->c_valid = FALSE;                                    /* neuer liegt weit früher: nahtlos anschließen, neuen Bezug nehmen */
          }
          const GstFlowReturn fr = pb_sel_gap_locked(self, a_rate, a_ch, offset == self->c_ref - p_new && diff > (gint64) SEL_HOLE ? (GstClockTime) MAX(out_ideal, 0) : GST_CLOCK_TIME_NONE);   /* letzten Puffer des alten ausblenden, ggf. Stille bis zum neuen */
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
      if (self->force_key)                                           /* Schnitt: der Encoder soll einen vollständigen Bildanfang setzen */
        gst_pad_push_event(self->src, gst_video_event_new_downstream_force_key_unit(
            GST_CLOCK_TIME_NONE, GST_CLOCK_TIME_NONE, GST_CLOCK_TIME_NONE, TRUE, 0));
      self->switches++;
    }
    self->sent_pad = idx;
    self->dropped_ns = 0;
    self->prev_pts = GST_CLOCK_TIME_NONE;
  } else if (!self->sent_caps || (self->caps[idx] && !gst_caps_is_equal(self->sent_caps, self->caps[idx]))) {
    pb_sel_push_sticky_locked(self, idx);                            /* Format hat sich beim gewählten Eingang geändert */
  }
  buf = gst_buffer_make_writable(buf);
  const GstClockTime pts = GST_BUFFER_PTS(buf), dts = GST_BUFFER_DTS(buf);
  if (GST_CLOCK_TIME_IS_VALID(pts)) {
    gint64 o = (gint64) pts + self->offset;
    if (o < 0)
      o = 0;
    if (self->first_after_switch) {                                  /* erster Puffer nach dem Umschalten (Ton): nicht vor das Ende des alten */
      if (self->have_next && o < (gint64) self->next_out)
        o = (gint64) self->next_out;
      self->first_after_switch = FALSE;
    }
    if (self->after_fill) {                                          /* der Eingang ist nach dem Füllen zurück: nahtlos an das Gefüllte anschließen */
      if (self->have_next && o < (gint64) self->next_out)
        o = (gint64) self->next_out;
      else if (audio_fx && self->have_next && o > (gint64) self->next_out + SEL_SILENCE_MIN) {
        const GstFlowReturn gr = pb_sel_gap_locked(self, a_rate, a_ch, (GstClockTime) o);    /* Ton: Lücke bis zum ersten echten Puffer mit Stille füllen */
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
  self->filling = FALSE;                                             /* echte Daten: es wird nicht mehr gefüllt, die Frist beginnt neu */
  self->last_push_rt = pb_sel_running_time((GstElement *) self);
  if (audio_fx) {                                                    /* Ton: einen Puffer zurückhalten (Ausblendung beim nächsten Umschalten) */
    if (self->fade_in) {
      buf = pb_sel_fade(buf, TRUE, a_ch);
      self->fade_in = FALSE;
    }
    GstBuffer *prev = self->held;
    self->held = buf;
    ret = prev ? gst_pad_push(self->src, prev) : GST_FLOW_OK;
  } else {
    if (self->held) {                                                /* Format hat gewechselt: zurückgehaltenen Puffer zuerst weitergeben */
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
      g_mutex_unlock(&self->lock);                                   /* nach hinten geht es mit dem nächsten Puffer */
      gst_event_unref(event);
      break;
    }
    case GST_EVENT_EOS:
      if (act) {                                                     /* der gewählte Eingang ist zu Ende: Ende weiterreichen */
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
        gst_event_unref(event);                                      /* wartende Kamera weg: stört die Sendung nicht */
      }
      break;
    case GST_EVENT_FLUSH_START:
      if (act) {
        ret = gst_pad_push_event(self->src, event);                  /* zuerst weitergeben (entsperrt einen hängenden Push), dann den Rest verwerfen */
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
    default:                                                         /* Datenstrom-Anfang, Segment, Tags, Lücken usw. der Eingänge: eigene Regie */
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

/* ------------------------------------------------------------------ pbctl */

typedef struct {
  GstElement parent;
  gchar *file, *video_queue, *audio_queue, *pip_queue, *pip2_queue, *pip3_queue;
  gchar *cam[4];                  /* Tausch ohne Unterbrechung: je Kamera eine Liste "queue:art,..." (v = Bild groß, s = Bild klein, a = Ton) */
  gchar *selector, *audio_selector, *select_file, *state_file;
  gint audio_pos;                 /* -1: Ton der Hauptkamera, 0 bis 2: Ton der Kamera an dieser Stelle */
  gchar *view_file, *view_state_file, *mixer_name, *volume_name;   /* Ansicht im Betrieb: Sichtbarkeit, Tonquelle, Stumm (siehe pb_ctl_poll_view) */
  gchar *live_file;               /* Hier meldet pbctl, welche Kameras gerade Bilder liefern: vier Zahlen 0/1 (Kamera 0 bis 3) */
  gchar live_last[16];
  gint64 live_written_rt;
  gchar *style_base[3];           /* Stil der Stellen 1 bis 3, wie die Pipeline ihn gebaut hat (beim ersten Durchlauf gemerkt) */
  gboolean view_inited;
  gint view_hide;                 /* zuletzt gestellt: Bit 0 bis 2 = kleines Bild an Stelle 1 bis 3 ausgeblendet */
  gint last_v[4];                 /* zuletzt gestellter Zustand des Umschalters (für den Ton), gültig wenn have_v */
  gboolean have_v;
  GThread *thread;
  volatile gint run;
  gint64 last_mtime, sel_mtime, view_mtime;
} PbCtl;
typedef struct { GstElementClass parent_class; } PbCtlClass;
G_DEFINE_TYPE(PbCtl, pb_ctl, GST_TYPE_ELEMENT)

enum { CTL_0, CTL_FILE, CTL_VQ, CTL_AQ, CTL_PQ, CTL_P2Q, CTL_P3Q, CTL_CAM0, CTL_CAM1, CTL_CAM2, CTL_CAM3,
       CTL_SEL, CTL_ASEL, CTL_SELFILE, CTL_STATEFILE, CTL_VIEWFILE, CTL_VIEWSTATE, CTL_MIXER, CTL_VOLUME, CTL_APOS, CTL_LIVE };

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

/* Liste "queue:art,queue:art" einer Kamera abarbeiten */
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

/* Zustand in die Datei für die Oberfläche schreiben: "Hauptkamera Kamera-an-Stelle-1 -2 -3" */
static void pb_ctl_publish(PbCtl *self, const int v[4]) {
  if (!self->state_file || !*self->state_file)
    return;
  gchar *txt = g_strdup_printf("%d %d %d %d\n", v[0], v[1], v[2], v[3]);
  if (g_file_set_contents(self->state_file, txt, -1, NULL))
    g_chmod(self->state_file, 0644);
  g_free(txt);
}

/* Umschalten: Bild-Umschalter (und, falls vorhanden, Ton-Umschalter) auf den neuen Zustand stellen */
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

/* Zustand aus der Datei übernehmen, sobald sie sich ändert; beim ersten Durchlauf ohne Datei den eingebauten Zustand melden */
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
  if (first) {                                                     /* keine (brauchbare) Datei: Zustand des Umschalters melden */
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

/* ---- Ansicht im Betrieb: kleine Bilder ein-/ausblenden, Tonquelle, Stumm -------------------------------------------------
 * Steuerdatei view-file mit drei Zahlen "ausgeblendet Tonquelle stumm":
 *   ausgeblendet: Bit 0 bis 2 = kleines Bild an Stelle 1 bis 3 nicht zeichnen (0 = alle sichtbar)
 *   Tonquelle:    -1 Ton der Hauptkamera, 0 bis 2 Ton der Kamera an Stelle 1 bis 3 (nur mit Ton-Umschalter; sonst bleibt es wie gebaut)
 *   stumm:        0 oder 1 (Element "volume" mit dem Namen aus der Eigenschaft volume)
 * Nach dem Übernehmen schreibt pbctl denselben Zustand, wie er jetzt WIRKLICH gilt, in view-state-file. Was sich nicht stellen lässt
 * (kein Mischer, kein Ton-Umschalter, kein volume), bleibt dort wie es ist: die Oberfläche merkt es und startet dann neu. */

/* Text ohne "op=..."-Teile (Schlüssel wird ohne Leerzeichen verglichen) */
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

/* Gilt im Text am Ende Deckkraft 0 (Bild nicht zeichnen)? Bei doppeltem Schlüssel gilt der letzte (wie im Stil-Parser). */
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

/* Stil der Stellen 1 bis 3 beim ersten Durchlauf merken (so, wie die Pipeline ihn gebaut hat) */
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
      self->view_hide |= 1 << i;                                    /* schon ausgeblendet gebaut */
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
  if (first) {                                                       /* keine (brauchbare) Datei: Zustand melden, nichts ändern */
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
}

/* Welche Kameras liefern gerade Bilder? Vier Zahlen "1 0 1 0" in live-file, nur bei Änderung und alle 2 s (damit man sieht, dass pbctl lebt). */
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
    pb_ctl_publish_live(self);                                     /* Kameras mit Bild: alle 0,1 s prüfen */
    if (tick % 3 == 0)                                             /* Verzögerung: alle 0,3 s */
      pb_ctl_poll_delay(self);
    pb_ctl_poll_select(self, tick == 0);                           /* Umschalten: alle 0,1 s */
    pb_ctl_poll_view(self, tick == 0);                             /* Ansicht (ein-/ausblenden, Ton, stumm): alle 0,1 s, nach dem Umschalten */
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

/* ------------------------------------------------------------------ Plugin */

static gboolean plugin_init(GstPlugin *plugin) {
  GST_DEBUG_CATEGORY_INIT(pbpip_debug, "pbpip", 0, "IRL4YOU PiP");
  return gst_element_register(plugin, "pbpipsink", GST_RANK_NONE, pb_pip_sink_get_type()) &&
         gst_element_register(plugin, "pbpipmix", GST_RANK_NONE, pb_pip_mix_get_type()) &&
         gst_element_register(plugin, "pbpipsel", GST_RANK_NONE, pb_pip_sel_get_type()) &&
         gst_element_register(plugin, "pbctl", GST_RANK_NONE, pb_ctl_get_type());
}

#ifndef PACKAGE
#define PACKAGE "irl4you-pip"
#endif
GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, pbpip,
                  "IRL4YOU Bild-in-Bild: schreibt das kleine Bild in das Hauptbild",
                  plugin_init, "0.1", "MIT/X11", "irl4you-pip", "https://github.com/IRL4YOU/irl4you-pip")
