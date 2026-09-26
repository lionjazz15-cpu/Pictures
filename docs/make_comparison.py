"""Render docs/comparison.png (needs matplotlib):  python docs/make_comparison.py"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import bpy, numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
import run_tests as rt
from precision_shrinkwrap import mesh_utils as mu
C = bpy.context
Z = 0.035
def section(P, tris):
    segs = []
    for t in tris:
        p = P[t]; d = p[:,2]-Z; pts=[]
        for a,b in ((0,1),(1,2),(2,0)):
            if (d[a]>0) != (d[b]>0):
                u = d[a]/(d[a]-d[b]); pts.append(p[a]+(p[b]-p[a])*u)
        if len(pts)==2: segs.append(pts)
    return np.array(segs)
def world_eval(o):
    dg = C.evaluated_depsgraph_get(); ev=o.evaluated_get(dg); me=ev.to_mesh()
    P = mu.to_world(mu.mesh_coords(me), o.matrix_world); T = mu.mesh_triangles(me); ev.to_mesh_clear(); return P,T
rt.reset()
body = rt.make_body(); g = rt.make_garment(res=(160,64))
s = C.scene.precision_shrinkwrap; s.target = body; s.offset = 0.003
bodyP, bodyT = world_eval(body)
res = {}
g2 = g.copy(); g2.data = g.data.copy(); C.scene.collection.objects.link(g2)
m = g2.modifiers.new("SW","SHRINKWRAP"); m.target = body; m.offset = 0.003
res["Blender Shrinkwrap (Nearest Surface, offset 3mm)"] = world_eval(g2)
g2.modifiers[0].wrap_mode = "OUTSIDE_SURFACE"
res["Blender Shrinkwrap (Outside Surface, offset 3mm)"] = world_eval(g2)
bpy.data.objects.remove(g2)
bpy.ops.object.select_all(action="DESELECT"); g.select_set(True)
s.output = "SHAPE_KEY"; s.tension = 0; bpy.ops.precision_shrinkwrap.fit()
g.data.shape_keys.key_blocks[-1].value = 1.0
res["Precision Shrinkwrap (offset 3mm)"] = world_eval(g)
g.shape_key_clear(); s.tension = 25; bpy.ops.precision_shrinkwrap.fit()
res["Precision Shrinkwrap + Valley Tension 25"] = world_eval(g)
fig, axes = plt.subplots(1, 4, figsize=(20, 5.6))
bs = section(bodyP, bodyT)
for ax, (name, (P,T)) in zip(axes, res.items()):
    for sg in bs: ax.plot(sg[:,0], sg[:,1], color="#999", lw=2)
    gs = section(P, T)
    for sg in gs: ax.plot(sg[:,0], sg[:,1], color="#d62728", lw=1)
    pts = gs.reshape(-1,3); ax.plot(pts[:,0], pts[:,1], ".", color="#1f77b4", ms=3)
    ax.set_xlim(-0.075, 0.075); ax.set_ylim(0.095, 0.235); ax.set_aspect("equal"); ax.set_title(name, fontsize=10)
    ax.set_xticks([]); ax.set_yticks([])
fig.suptitle(f"Cross-section through the crease (z = {Z}) — grey: body, red: garment, dots: garment edge crossings", fontsize=11)
plt.tight_layout(); plt.savefig(os.path.join(ROOT, "docs", "comparison.png"), dpi=110)
print("saved")
