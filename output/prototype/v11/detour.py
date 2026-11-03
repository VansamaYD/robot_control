"""Bounded bidirectional joint-space search. Not continuous mesh collision."""
import math
import numpy as np
from errors import MotionError


def search(start, goal, low, high, valid, guard=lambda: None, max_nodes=500,
           step=.22, resolution=.005, seed=1701, progress=lambda _: None):
    """RRT-Connect with deterministic sampling and checked straight edges.

    Each edge checks both endpoints and intermediate states. Every output still
    needs dense time parameterization, collision and fluid validation by Engine.
    """
    start, goal = np.asarray(start, float), np.asarray(goal, float)
    low, high = np.asarray(low, float), np.asarray(high, float)
    stats = {'algorithm': 'rrt_connect', 'nodes': 2, 'state_checks': 0}

    def state(q):
        guard()
        stats['state_checks'] += 1
        return np.isfinite(q).all() and np.all(q >= low) and np.all(q <= high) and valid(q)

    def edge(a, b):
        count = max(1, math.ceil(float(np.max(np.abs(b-a)))/resolution))
        return all(state(a+(b-a)*(i/count)) for i in range(count+1))

    if not state(start) or not state(goal):
        raise MotionError('DETOUR_ENDPOINT_INVALID', stats)
    if edge(start, goal):
        return [start.copy(), goal.copy()], {**stats, 'waypoints': 2}
    trees = [([start.copy()], [-1]), ([goal.copy()], [-1])]
    rng = np.random.default_rng(seed)
    active = 0

    def extend(tree, target):
        nodes, parents = tree
        distance = np.linalg.norm((np.asarray(nodes)-target)/(high-low), axis=1)
        near = int(np.argmin(distance))
        delta = target-nodes[near]
        length = np.linalg.norm(delta)
        if length < 1e-10:
            return near, True
        q = nodes[near]+delta*min(1., step/length)
        if not edge(nodes[near], q):
            return None, False
        nodes.append(q); parents.append(near)
        stats['nodes'] += 1
        return len(nodes)-1, length <= step

    def ancestry(tree, index):
        nodes, parents = tree
        path = []
        while index != -1:
            path.append(nodes[index]); index = parents[index]
        return path[::-1]

    while stats['nodes'] < max_nodes:
        guard()
        a, b = trees[active], trees[1-active]
        target = b[0][0] if rng.random() < .2 else rng.uniform(low, high)
        index, _ = extend(a, target)
        if index is not None:
            while stats['nodes'] < max_nodes:
                other, reached = extend(b, a[0][index])
                if other is None: break
                if reached:
                    left, right = ancestry(a, index), ancestry(b, other)
                    path = left + right[::-1][1:]
                    if active: path = path[::-1]
                    # Greedy, checked shortcutting; cannot jump across obstacles.
                    shortened, i = [path[0]], 0
                    while i < len(path)-1:
                        for j in range(len(path)-1, i, -1):
                            if edge(path[i], path[j]): break
                        shortened.append(path[j]); i = j
                    return shortened, {**stats, 'waypoints': len(shortened)}
        active = 1-active
        # Count iterations too: blocked expansions add no nodes.
        stats['iterations'] = stats.get('iterations', 0)+1
        if stats['iterations'] % 10 == 0: progress(dict(stats))
        if stats['iterations'] >= max_nodes*2: break
    raise MotionError('DETOUR_NOT_FOUND', stats)
