"""Tiny QR encoder: byte mode, ECC level L, versions 1-10, fixed mask 0. Returns SVG text."""

# (ECC codewords per block, number of blocks) for versions 1..10 at level L
ECC = [(7, 1), (10, 1), (15, 1), (20, 1), (26, 1), (18, 2), (20, 2), (24, 2), (30, 2), (18, 4)]


def _mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs(data, degree):
    div = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            div[j] = _mul(div[j], root)
            if j + 1 < degree:
                div[j] ^= div[j + 1]
        root = _mul(root, 2)
    rem = [0] * degree
    for b in data:
        f = b ^ rem.pop(0)
        rem.append(0)
        for i, c in enumerate(div):
            rem[i] ^= _mul(c, f)
    return rem


def _raw(ver):
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        a = ver // 7 + 2
        n -= (25 * a - 10) * a - 55
        if ver >= 7:
            n -= 36
    return n // 8


def qr_svg(text):
    data = text.encode()
    for ver in range(1, 11):
        ecc, nb = ECC[ver - 1]
        cap = _raw(ver) - ecc * nb
        if 4 + (8 if ver < 10 else 16) + 8 * len(data) <= cap * 8:
            break
    else:
        raise ValueError("text too long for this encoder")

    bits = "0100" + format(len(data), "08b" if ver < 10 else "016b") + "".join(format(b, "08b") for b in data)
    bits += "0" * min(4, cap * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    words = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < cap:
        words.append(pad)
        pad ^= 0xEC ^ 0x11

    raw = _raw(ver)
    short, slen = nb - raw % nb, raw // nb
    blocks, k = [], 0
    for i in range(nb):
        d = words[k:k + slen - ecc + (i >= short)]
        k += len(d)
        blocks.append(d + ([0] if i < short else []) + _rs(d, ecc))
    out = []
    for i in range(len(blocks[0])):
        for j, b in enumerate(blocks):
            if i != slen - ecc or j >= short:
                out.append(b[i])

    n = ver * 4 + 17
    m = [[False] * n for _ in range(n)]
    fn = [[False] * n for _ in range(n)]

    def put(x, y, v):
        m[y][x] = bool(v)
        fn[y][x] = True

    for i in range(n):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if 0 <= cx + dx < n and 0 <= cy + dy < n:
                    put(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
    if ver > 1:
        a = ver // 7 + 2
        step = (ver * 4 + a * 2 + 1) // (a * 2 - 2) * 2
        pos = [6] + sorted(range(n - 7, 6, -step))
        for i, cy in enumerate(pos):
            for j, cx in enumerate(pos):
                if (i, j) in ((0, 0), (0, a - 1), (a - 1, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    # format bits: level L = 01, mask 0 (masking never touches function modules)
    d = 1 << 3
    r = d
    for _ in range(10):
        r = (r << 1) ^ ((r >> 9) * 0x537)
    f = (d << 10 | r) ^ 0x5412
    for i in range(6):
        put(8, i, (f >> i) & 1)
    put(8, 7, (f >> 6) & 1)
    put(8, 8, (f >> 7) & 1)
    put(7, 8, (f >> 8) & 1)
    for i in range(9, 15):
        put(14 - i, 8, (f >> i) & 1)
    for i in range(8):
        put(n - 1 - i, 8, (f >> i) & 1)
    for i in range(8, 15):
        put(8, n - 15 + i, (f >> i) & 1)
    put(8, n - 8, True)
    if ver >= 7:
        r = ver
        for _ in range(12):
            r = (r << 1) ^ ((r >> 11) * 0x1F25)
        v = ver << 12 | r
        for i in range(18):
            put(n - 11 + i % 3, i // 3, (v >> i) & 1)
            put(i // 3, n - 11 + i % 3, (v >> i) & 1)

    i = 0
    for right in range(n - 1, 0, -2):
        if right <= 6:
            right -= 1
        for v in range(n):
            for j in range(2):
                x = right - j
                y = n - 1 - v if (right + 1) & 2 == 0 else v
                if not fn[y][x] and i < len(out) * 8:
                    m[y][x] = bool((out[i >> 3] >> (7 - (i & 7))) & 1)
                    i += 1
    for y in range(n):
        for x in range(n):
            if not fn[y][x] and (x + y) % 2 == 0:
                m[y][x] = not m[y][x]

    q, s = 4, n + 8
    path = "".join(f"M{x + q} {y + q}h1v1h-1z" for y in range(n) for x in range(n) if m[y][x])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {s} {s}" shape-rendering="crispEdges">'
            f'<rect width="{s}" height="{s}" fill="#fff"/><path fill="#000" d="{path}"/></svg>')
