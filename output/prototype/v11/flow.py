"""Trusted example, also exposed as a versioned declarative recipe."""


def main(robot):
    yield robot.scoop('舀取 30mL 汤汁', 30, .7)
    yield robot.move_tcp('提勺', [0, -0.100, 0.195], [0, 0, 1], 12.2)
    yield robot.move_tcp('移到鸡背上方', [0, -0.020, 0.195], [0, 0, 1], 6.5)
    yield robot.move_tcp('倾斜汤勺 65°', [0, -0.020, 0.195], [0.906307787, 0, 0.422618262], 5.0)
    yield robot.wait('保持倾角淋汁', 2.5)
    yield robot.move_tcp('汤勺回正', [0, -0.020, 0.195], [0, 0, 1], 4.0)
    yield robot.move_tcp('返回取汁上方', [0, -0.100, 0.195], [0, 0, 1], 6.5)
    yield robot.move_tcp('下降到取汁点', [0, -0.100, 0.075], [0, 0, 1], 12.2)
