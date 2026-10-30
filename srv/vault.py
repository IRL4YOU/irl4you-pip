
import base64
import binascii
import hashlib
import hmac
import secrets


class Vault:
    ITERATIONS = 600000
    MIN_ITER, MAX_ITER = 100000, 2000000
    TAG = b"IRL4YOU-BOX-SETTINGS-1|"
    MIN_PASSWORD, MAX_PASSWORD = 8, 128
    _TABLES = None

    @classmethod
    def _tables(cls):
        if cls._TABLES:
            return cls._TABLES
        sbox = [0] * 256
        rot = lambda v, n: ((v << n) | (v >> (8 - n))) & 0xFF
        p = q = 1
        while True:
            p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF
            q ^= (q << 1) & 0xFF
            q ^= (q << 2) & 0xFF
            q ^= (q << 4) & 0xFF
            if q & 0x80:
                q ^= 0x09
            sbox[p] = (q ^ rot(q, 1) ^ rot(q, 2) ^ rot(q, 3) ^ rot(q, 4) ^ 0x63) & 0xFF
            if p == 1:
                break
        sbox[0] = 0x63
        t0 = []
        for a in range(256):
            s = sbox[a]
            s2 = ((s << 1) ^ (0x1B if s & 0x80 else 0)) & 0xFF
            t0.append((s2 << 24) | (s << 16) | (s << 8) | (s2 ^ s))
        ror = lambda w, n: ((w >> n) | (w << (32 - n))) & 0xFFFFFFFF
        cls._TABLES = (sbox, t0, [ror(w, 8) for w in t0], [ror(w, 16) for w in t0], [ror(w, 24) for w in t0])
        return cls._TABLES

    @classmethod
    def _round_keys(cls, key):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("AES-256 braucht einen Schlüssel von 32 Byte")
        sbox = cls._tables()[0]
        sub = lambda w: (sbox[w >> 24] << 24) | (sbox[(w >> 16) & 255] << 16) | (sbox[(w >> 8) & 255] << 8) | sbox[w & 255]
        w = [int.from_bytes(key[i:i + 4], "big") for i in range(0, 32, 4)]
        rcon = 1
        for i in range(8, 60):
            t = w[i - 1]
            if i % 8 == 0:
                t = sub(((t << 8) | (t >> 24)) & 0xFFFFFFFF) ^ (rcon << 24)
                rcon = ((rcon << 1) ^ (0x11B if rcon & 0x80 else 0)) & 0xFF
            elif i % 8 == 4:
                t = sub(t)
            w.append(w[i - 8] ^ t)
        return w

    @classmethod
    def aes256_block(cls, rk, block):
        sbox, t0, t1, t2, t3 = cls._tables()
        s0, s1, s2, s3 = (int.from_bytes(block[i:i + 4], "big") ^ rk[i // 4] for i in (0, 4, 8, 12))
        for r in range(1, 14):
            k = 4 * r
            s0, s1, s2, s3 = (t0[s0 >> 24] ^ t1[(s1 >> 16) & 255] ^ t2[(s2 >> 8) & 255] ^ t3[s3 & 255] ^ rk[k],
                              t0[s1 >> 24] ^ t1[(s2 >> 16) & 255] ^ t2[(s3 >> 8) & 255] ^ t3[s0 & 255] ^ rk[k + 1],
                              t0[s2 >> 24] ^ t1[(s3 >> 16) & 255] ^ t2[(s0 >> 8) & 255] ^ t3[s1 & 255] ^ rk[k + 2],
                              t0[s3 >> 24] ^ t1[(s0 >> 16) & 255] ^ t2[(s1 >> 8) & 255] ^ t3[s2 & 255] ^ rk[k + 3])
        k = 56
        out = ((sbox[s0 >> 24] << 24) | (sbox[(s1 >> 16) & 255] << 16) | (sbox[(s2 >> 8) & 255] << 8) | sbox[s3 & 255]) ^ rk[k], \
              ((sbox[s1 >> 24] << 24) | (sbox[(s2 >> 16) & 255] << 16) | (sbox[(s3 >> 8) & 255] << 8) | sbox[s0 & 255]) ^ rk[k + 1], \
              ((sbox[s2 >> 24] << 24) | (sbox[(s3 >> 16) & 255] << 16) | (sbox[(s0 >> 8) & 255] << 8) | sbox[s1 & 255]) ^ rk[k + 2], \
              ((sbox[s3 >> 24] << 24) | (sbox[(s0 >> 16) & 255] << 16) | (sbox[(s1 >> 8) & 255] << 8) | sbox[s2 & 255]) ^ rk[k + 3]
        return b"".join(v.to_bytes(4, "big") for v in out)

    @classmethod
    def ctr(cls, key, counter, data):
        rk = cls._round_keys(key)
        out = bytearray()
        for i in range(0, len(data), 16):
            ks = cls.aes256_block(rk, ((counter + i // 16) & ((1 << 128) - 1)).to_bytes(16, "big"))
            chunk = data[i:i + 16]
            n = len(chunk)
            out += (int.from_bytes(chunk, "big") ^ int.from_bytes(ks[:n], "big")).to_bytes(n, "big")
        return bytes(out)

    @classmethod
    def _keys(cls, password, salt, iterations):
        k = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, 64)
        return k[:32], k[32:]

    @classmethod
    def _mac(cls, mac_key, iterations, salt, nonce, data):
        return hmac.new(mac_key, cls.TAG + iterations.to_bytes(8, "big") + salt + nonce + data, hashlib.sha256).digest()

    @classmethod
    def check_password(cls, password):
        if not isinstance(password, str) or not cls.MIN_PASSWORD <= len(password) <= cls.MAX_PASSWORD:
            raise ValueError("Passwort: %d bis %d Zeichen" % (cls.MIN_PASSWORD, cls.MAX_PASSWORD))

    @classmethod
    def seal(cls, plain, password, header, iterations=None):
        cls.check_password(password)
        iterations = iterations or cls.ITERATIONS
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
        enc_key, mac_key = cls._keys(password, salt, iterations)
        data = cls.ctr(enc_key, int.from_bytes(nonce + b"\0\0\0\0", "big"), plain)
        b64 = lambda b: base64.b64encode(b).decode("ascii")
        return dict(header, encrypted=True, cipher="AES-256-CTR", kdf="PBKDF2-HMAC-SHA256", iterations=iterations,
                    salt=b64(salt), nonce=b64(nonce), data=b64(data), mac=b64(cls._mac(mac_key, iterations, salt, nonce, data)))

    @classmethod
    def open(cls, doc, password):
        cls.check_password(password)
        try:
            if doc.get("cipher") != "AES-256-CTR" or doc.get("kdf") != "PBKDF2-HMAC-SHA256":
                raise ValueError
            iterations = doc.get("iterations")
            if isinstance(iterations, bool) or not isinstance(iterations, int) or not cls.MIN_ITER <= iterations <= cls.MAX_ITER:
                raise ValueError
            salt, nonce, data, mac = (base64.b64decode(str(doc.get(k, "")), validate=True) for k in ("salt", "nonce", "data", "mac"))
            if len(salt) != 16 or len(nonce) != 12 or len(mac) != 32:
                raise ValueError
        except (ValueError, TypeError, binascii.Error):
            raise ValueError("Die Datei ist keine gültige verschlüsselte Sicherung")
        enc_key, mac_key = cls._keys(password, salt, iterations)
        if not hmac.compare_digest(mac, cls._mac(mac_key, iterations, salt, nonce, data)):
            raise ValueError("Das Passwort ist falsch oder die Datei wurde verändert")
        return cls.ctr(enc_key, int.from_bytes(nonce + b"\0\0\0\0", "big"), data)
