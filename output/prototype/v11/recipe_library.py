"""Curated three-zone example; coordinates are fixed-scene assumptions."""
import math

THREE_ZONES = [
    {'key': 'left', 'label': '左侧', 'p': [-.060, .040, .195], 'sign': 1},
    {'key': 'center', 'label': '中部', 'p': [0., .030, .195], 'sign': 1},
    {'key': 'right', 'label': '右侧', 'p': [.060, .040, .195], 'sign': -1},
]


def three_zone_actions():
    actions = []
    def move(name, p, n, duration):
        return {'kind': 'move_tcp', 'name': name, 'p': list(p), 'n': list(n), 'duration_s': duration}
    for zone in THREE_ZONES:
        label, p = zone['label'], zone['p']
        tilt = [zone['sign']*math.sin(math.radians(65)), 0., math.cos(math.radians(65))]
        actions += [
            {'kind': 'scoop', 'name': label+'：重新取汁 ≥30mL', 'target_m3': 30e-6, 'duration_s': .7},
            move(label+'：提勺', [0., -.1, .195], [0,0,1], 12.2),
            move(label+'：移至浇汁点', p, [0,0,1], 9.),
            move(label+'：倾倒65°', p, tilt, 6.),
            {'kind': 'wait', 'name': label+'：保持淋汁', 'duration_s': 2.5},
            move(label+'：原位回正', p, [0,0,1], 6.),
            move(label+'：返回取汁上方', [0., -.1, .195], [0,0,1], 9.),
            move(label+'：下降补汁', [0., -.1, .075], [0,0,1], 12.2),
        ]
    return actions
