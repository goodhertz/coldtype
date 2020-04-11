from coldtype.test import *


@test()
def test_projection(r):
    shape = DATPen().rect(300, 300).difference(DATPen().oval((-150, -150, 300, 300)))
    return DATPenSet([
        shape.copy().castshadow(-35, 200).f("hr", 0.65, 0.25),
        shape.copy().project(-35, 200).f("hr", 0.5, 0.5),
        shape.f("hr", 0.75, 0.75)
    ]).align(r)


@test()
def test_roughen(r):
    return DATPen().oval(r.inset(100)).flatten(20).roughen(20).smooth().f("hr",0.5,0.5)


@test()
def test_catmull(r):
    dp = DATPen()
    points = []
    last_pt = (0, 0)
    rnd = Random()
    for x in range(0, 10):
        too_close = True
        while too_close:
            pt = (rnd.randint(0, r.w), rnd.randint(0, r.h))
            if abs(last_pt[0] - pt[0]) > 100 and abs(last_pt[1] - pt[1]) > 100:
                too_close = False
            last_pt = pt
        points.append(last_pt)
    return dp.catmull(points).endPath().f(None).s("random").sw(20)


@test()
def test_semicircles(r):
    return DATPen().semicircle(r.square(), "mxx", 10)