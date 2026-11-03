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
EMPTY_TILT = [math.sin(math.radians(85)), 0, math.cos(math.radians(85))]
DRY_LIMIT_M3 = 1e-9  # 0.001mL; model-only criterion, not a hardware safety rating.


def move(name, p, seconds=8., normal=UP):
    return {'kind': 'move_tcp', 'name': name, 'p': list(p), 'n': list(normal), 'duration_s': seconds}


def recovery_candidates(mode, current_tcp, current_normal, ladle_m3, current_q=None):
    direct = [{'kind': 'move_joints', 'name': '经检查返回取汁初态', 'q_rad': HOME_Q.tolist(), 'duration_s': 10.}]
    upper = [current_tcp[0], current_tcp[1], max(.220, current_tcp[2]+.060)]
    elevated = [move('原位置抬升', upper, 10., current_normal), move('高位回正汤勺', upper, 8.),
                move('回到取汁上方', LIFT, 10.), move('下降到取汁初态', PICK, 12.2)]
    reverse = [move('抬离翻鸡停靠点', ABOVE, 5.), move('返回锅外中转', GATE, 10.),
               move('返回取汁上方', LIFT, 10.), move('下降到取汁初态', PICK, 12.2)]
    prefixes = [reverse, direct, elevated] if np.linalg.norm(np.asarray(current_tcp)-DOCK) < .04 else [direct, elevated]
    if mode == 'home':
        if current_q is not None and np.allclose(current_q, HOME_Q, atol=1e-7):
            prefixes = [[]]
        return [prefix + [{'kind': 'wait', 'name': '让勺口与汤汁自然平衡', 'duration_s': .6}] for prefix in prefixes]
    # Docking never visits PICK. Even a cup empty when requested can fill on
    # approach, so drainage and the dry checkpoint are unconditional.
    high = [current_tcp[0], current_tcp[1], max(.195, current_tcp[2])]
    approach = [move('原位置抬离液面（保持勺姿）', high, 12.2, current_normal),
                move('移至锅内排汁上方', LIFT, 8.)]
    alternate = [move('原位置抬升', upper, 10., current_normal), move('在高位回正汤勺', upper, 8.),
                 move('移至锅内排汁上方', LIFT, 8.)]
    prefixes = [approach, alternate]
    if np.linalg.norm(np.asarray(current_tcp)-DOCK) < .04:
        prefixes.insert(0, [move('抬离停靠位置', ABOVE, 4.), move('经锅外中转', GATE, 8.), move('移至锅内排汁上方', LIFT, 8.)])
    suffix = [move('下降至干燥的锅内排汁点', [0, -.100, .160], 4.),
              move('锅内倾倒至85°', [0, -.100, .160], 6., EMPTY_TILT),
              {'kind': 'wait', 'name': '等待杯内余汁与空中液流落回锅内', 'duration_s': 3.},
              {'kind': 'verify_dry', 'name': '检查近空：未通过禁止离锅', 'duration_s': .1},
              move('近空后回正', [0, -.100, .160], 6.), move('抬至干燥通道', LIFT, 4.),
              move('近空检查后退出锅口', GATE, 8.), move('到翻鸡泊位上方', ABOVE, 8.), move('进入翻鸡停靠点', DOCK, 4.)]
    for action in suffix[4:]:
        action['requires_dry'] = True
    result = []
    for prefix in prefixes:
        actions = prefix + suffix
        pruned, p, normal = [], np.array(current_tcp), np.array(current_normal)
        for action in actions:
            if action['kind'] == 'move_tcp':
                same = np.linalg.norm(np.array(action['p'])-p) < 1e-6 and np.linalg.norm(np.array(action['n'])-normal) < 1e-6
                p, normal = np.array(action['p']), np.array(action['n'])
                if same: continue
            pruned.append(action)
        result.append(pruned)
    return result
