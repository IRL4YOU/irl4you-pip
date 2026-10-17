import json
import numpy as np
import cv2


def _spec(models):
    try:
        with open(models + "/modell.json") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    return d.get("yolox", "yolox_s"), d.get("plate", "plate_raw"), (int(d.get("yolox_size", 640)), int(d.get("plate_size", 608)))


class OnnxBackend:
    def __init__(self, models, threads=4):
        import onnxruntime as ort
        o = ort.SessionOptions()
        o.intra_op_num_threads = threads
        prov = ["CPUExecutionProvider"]
        yn, pn, self.sizes = _spec(models)
        self.yx = ort.InferenceSession(models + "/" + yn + ".onnx", o, providers=prov)
        self.pl = ort.InferenceSession(models + "/" + pn + ".onnx", o, providers=prov)
        self.face_model = models + "/yunet.onnx"
        self.name = "onnx"

    def yolox(self, tiles):
        return [self.yx.run(None, {"images": t.transpose(2, 0, 1)[None].astype(np.float32)})[0][0] for t in tiles]

    def plates(self, crops_rgb):
        return [self.pl.run(None, {"images": c.transpose(2, 0, 1)[None].astype(np.float32) / 255.0})[0][0] for c in crops_rgb]


class RknnBackend:

    def __init__(self, models, cores=3):
        from concurrent.futures import ThreadPoolExecutor
        from rknnlite.api import RKNNLite
        masks = [RKNNLite.NPU_CORE_0, RKNNLite.NPU_CORE_1, RKNNLite.NPU_CORE_2][:cores]
        self.yx, self.pl = [], []
        yn, pn, self.sizes = _spec(models)
        for m in masks:
            a = RKNNLite(verbose=False)
            a.load_rknn(models + "/" + yn + "_fp16.rknn")
            a.init_runtime(core_mask=m)
            b = RKNNLite(verbose=False)
            b.load_rknn(models + "/" + pn + "_fp16.rknn")
            b.init_runtime(core_mask=m)
            self.yx.append(a)
            self.pl.append(b)
        self.face_model = models + "/yunet.onnx"
        self.pool = ThreadPoolExecutor(max_workers=cores)
        self.name = "rknn%d" % cores

    def _map(self, rts, arrays, fix):
        n = len(rts)
        chunks = [arrays[i::n] for i in range(n)]
        futs = [self.pool.submit(lambda rt, ch: [rt.inference(inputs=[fix(a)[None]], data_format="nhwc")[0][0] for a in ch], rts[i], chunks[i]) for i in range(n)]
        res = [None] * len(arrays)
        for i, f in enumerate(futs):
            for j, o in enumerate(f.result()):
                res[i + j * n] = o
        return res

    def yolox(self, tiles):
        return self._map(self.yx, tiles, lambda a: a)

    def plates(self, crops_rgb):
        return self._map(self.pl, crops_rgb, lambda a: a)
