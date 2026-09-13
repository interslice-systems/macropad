"""Pixel-exact stereo faceplate and paged readout. No hardware dependencies."""
COLS = 20
PAGE_MS = 3000
STALE_MS = 6500
ROWS = 8
CELL_W, CELL_H = 7, 4
GRID_X, GRID_Y = 8, 22

# Original 5x7 uppercase faceplate lettering, row masks, left bit first.
# A fixed tile font avoids Label allocations while bars animate; the HTML
# preview consumes these same masks, so its text fits the real OLED too.
FONT = {
    ' ': (0,0,0,0,0,0,0),
    'A': (14,17,17,31,17,17,17), 'B': (30,17,17,30,17,17,30),
    'C': (14,17,16,16,16,17,14), 'D': (30,17,17,17,17,17,30),
    'E': (31,16,16,30,16,16,31), 'F': (31,16,16,30,16,16,16),
    'G': (14,17,16,23,17,17,15), 'H': (17,17,17,31,17,17,17),
    'I': (14,4,4,4,4,4,14), 'J': (7,2,2,2,18,18,12),
    'K': (17,18,20,24,20,18,17), 'L': (16,16,16,16,16,16,31),
    'M': (17,27,21,21,17,17,17), 'N': (17,25,25,21,19,19,17),
    'O': (14,17,17,17,17,17,14), 'P': (30,17,17,30,16,16,16),
    'Q': (14,17,17,17,21,18,13), 'R': (30,17,17,30,20,18,17),
    'S': (15,16,16,14,1,1,30), 'T': (31,4,4,4,4,4,4),
    'U': (17,17,17,17,17,17,14), 'V': (17,17,17,17,17,10,4),
    'W': (17,17,17,21,21,21,10), 'X': (17,17,10,4,10,17,17),
    'Y': (17,17,10,4,4,4,4), 'Z': (31,1,2,4,8,16,31),
    '0': (14,17,19,21,25,17,14), '1': (4,12,4,4,4,4,14),
    '2': (14,17,1,2,4,8,31), '3': (30,1,1,14,1,1,30),
    '4': (2,6,10,18,31,2,2), '5': (31,16,16,30,1,1,30),
    '6': (14,16,16,30,17,17,14), '7': (31,1,2,4,8,8,8),
    '8': (14,17,17,14,17,17,14), '9': (14,17,17,15,1,1,14),
    '?': (14,17,1,2,4,0,4), '!': (4,4,4,4,4,0,4),
    '-': (0,0,0,31,0,0,0), '.': (0,0,0,0,0,6,6),
    ',': (0,0,0,0,6,6,4), ':': (0,6,6,0,6,6,0),
    "'": (4,4,2,0,0,0,0), '"': (10,10,5,0,0,0,0),
    '/': (1,1,2,4,8,16,16), '\\': (16,16,8,4,2,1,1),
    '(': (2,4,8,8,8,4,2), ')': (8,4,2,2,2,4,8),
    '[': (14,8,8,8,8,8,14), ']': (14,2,2,2,2,2,14),
    '&': (12,18,20,8,21,18,13), '+': (0,4,4,31,4,4,0),
    '=': (0,0,31,0,31,0,0), '_': (0,0,0,0,0,0,31),
    '#': (10,31,10,10,31,10,0), '%': (25,25,2,4,8,19,19),
    '*': (0,21,14,31,14,21,0), '@': (14,17,23,21,23,16,14),
    '<': (2,4,8,16,8,4,2), '>': (8,4,2,1,2,4,8),
    '|': (4,4,4,4,4,4,4), '$': (4,15,20,14,5,30,4),
    ';': (0,6,6,0,6,6,4), '^': (4,10,17,0,0,0,0),
    '~': (0,0,9,22,0,0,0), '`': (8,4,0,0,0,0,0),
    '{': (2,4,4,8,4,4,2), '}': (8,4,4,2,4,4,8),
}
GLYPHS = ''.join(FONT)

TINY = {
    ' ': (0,0,0,0,0), '0': (7,5,5,5,7), '1': (2,6,2,2,7),
    '2': (7,1,7,4,7), '4': (5,5,7,1,1), '5': (7,4,7,1,7),
    '6': (7,4,7,5,7), 'K': (5,5,6,5,5), 'S': (7,4,7,1,7),
    'P': (6,5,6,4,4), 'E': (7,4,6,4,7), 'C': (7,4,4,4,7),
    'T': (7,2,2,2,2), 'R': (6,5,6,5,5), 'U': (5,5,5,5,7),
    'M': (5,7,7,5,5),
}


def backdrop():
    """Immutable bezel, play marker and logarithmic frequency legend."""
    pixels = set()
    for x in range(2,126):
        pixels.add((x,19))
        pixels.add((x,55))
    for y in range(21,54):
        pixels.add((0,y))
        pixels.add((127,y))
    pixels.update(((1,20),(126,20),(1,54),(126,54)))
    for y in (23,31,39,47,53):
        for x in (3,4,123,124):
            pixels.add((x,y))
    for x in range(5):
        for y in range(x//2, 5-x//2):
            pixels.add((x,y+1))
    pixels.update(((1,11),(2,11),(1,12),(2,12)))
    def text(s,x,y):
        for i,c in enumerate(s):
            for r,mask in enumerate(TINY[c]):
                for col in range(3):
                    if mask & (1 << (2-col)):
                        pixels.add((x+i*4+col,y+r))
    for s,x in (('50',1),('250',28),('1K',60),('4K',88),('16K',116)):
        text(s,x,58)
    return pixels


def level(value):
    return (value + 1) // 2  # 16 wire levels -> eight display segments


def tile(bar, peak, row):
    height = ROWS - row
    return int(height <= level(bar)) | (2 if level(peak) == height else 0)


def pages(text):
    """Word-aware held pages; long words still fit, without losing characters."""
    text = text.upper().strip()
    result = []
    while len(text) > COLS:
        split = text.rfind(' ', 0, COLS + 1)
        if split <= 0:
            split = COLS
        result.append(text[:split])
        text = text[split:].lstrip()
    result.append(text)
    return result


class Readout:
    def __init__(self, diff):
        self.diff = diff
        self.received = None
        self.epoch = 0
        self.value = ('', '')
        self.lines = (['SYSTEM AUDIO'], ['OPERATOR'])
        self.tune_until = None
        self.tune_lines = None

    def receive(self, msg, now):
        title, artist = msg.get('title'), msg.get('artist')
        if any(not isinstance(v, str) or len(v) > 96 or
               any(not 32 <= ord(c) < 127 for c in v) for v in (title,artist)):
            return False
        value = (title, artist)
        stale = self.received is None or self.diff(now,self.received) >= STALE_MS
        self.received = now
        if title and value != self.value:
            self.tune_until = None      # the player caught up: LOADING is over
        if value != self.value or stale:
            self.epoch = now
            self.value = value
            self.lines = (pages(title or 'SYSTEM AUDIO'), pages(artist or 'OPERATOR'))
        return True

    def tune(self, msg, now):
        """Preview the station under the knob for `hold` seconds."""
        title, line, hold = msg.get('title'), msg.get('line'), msg.get('hold')
        if any(not isinstance(v, str) or len(v) > 96 or
               any(not 32 <= ord(c) < 127 for c in v) for v in (title,line)):
            return False
        if type(hold) is not int or not 0 < hold <= 30:
            return False
        self.tune_until = now + hold * 1000
        self.tune_lines = (pages(title)[0], pages(line)[0])   # the first page names the station
        return True

    def tuning(self, now):
        return self.tune_until is not None and self.diff(self.tune_until,now) > 0

    def frame(self, now):
        if self.tuning(now):
            return self.tune_lines
        if self.received is None or self.diff(now,self.received) >= STALE_MS:
            return ('SYSTEM AUDIO', 'OPERATOR')
        page = max(0,self.diff(now,self.epoch)) // PAGE_MS
        return tuple(line[page % len(line)] for line in self.lines)
