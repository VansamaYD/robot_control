"""Candidate recovery routes; every full candidate is checked before execution."""
import math
import numpy as np
from scene_geometry import SCENE

HOME_Q = np.deg2rad([-.0386, 22.7339, 65.0816, -87.8151, 2.7893])
PICK = [0, -.100, .075]
LIFT = [0, -.100, .195]
GATE = [-.140, -.220, .220]
ABOVE = list(SCENE['parking']['above_m'])
DOCK = list(SCENE['parking']['position_m'])
UP = [0, 0, 1]
TILT = [math.sin(math.radians(65)), 0, math.cos(math.radians(65))]


def move(name, p, seconds=8., normal=UP):
    return {'kind': 'move_tcp', 'name': name, 'p': list(p), 'n': list(normal), 'duration_s': seconds}


def recovery_candidates(mode, current_tcp, current_normal, ladle_m3):
    direct = [{'kind': 'move_joints', 'name': '经检查返回取汁初态', 'q_rad': HOME_Q.tolist(), 'duration_s': 10.}]
    upper = [current_tcp[0], current_tcp[1], max(.220, current_tcp[2]+.060)]
    elevated = [move('原位置抬升', upper, 10., current_normal), move('高位回正汤勺', upper, 8.),
                move('回到取汁上方', LIFT, 10.), move('下降到取汁初态', PICK, 12.2)]
    reverse = [move('抬离翻鸡停靠点', ABOVE, 5.), move('返回锅外中转', GATE, 10.),
               move('返回取汁上方', LIFT, 10.), move('下降到取汁初态', PICK, 12.2)]
    prefixes = [reverse, direct, elevated] if np.linalg.norm(np.asarray(current_tcp)-DOCK) < .04 else [direct, elevated]
    if mode == 'home':
        return prefixes
    suffix = [move('抬至锅口上方', LIFT, 12.2)]
    if ladle_m3 > .5e-6:
        suffix += [move('移至锅内回汁点', [0, -.100, .160], 4.),
                   move('向锅内倒回余汁', [0, -.100, .160], 5., TILT),
                   {'kind': 'wait', 'name': '等待余汁回锅', 'duration_s': 3.},
                   move('回正汤勺', [0, -.100, .160], 5.), move('回到高位', LIFT, 4.)]
    suffix += [move('退出锅口', GATE, 10.), move('到翻鸡泊位上方', ABOVE, 10.), move('进入翻鸡停靠点', DOCK, 5.)]
    return [prefix + suffix for prefix in prefixes]
