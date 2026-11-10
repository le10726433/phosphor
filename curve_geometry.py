"""Analytic circular arcs and centripetal Catmull–Rom splines.

Spline parameterization: Yuksel/Schaefer/Keyser,
https://www.cemyuksel.com/research/catmullrom_param/
Tessellation error is bounded in local coordinates, never by camera zoom.
"""
from functools import lru_cache
import math
import numpy as np

CURVE_ERROR=.0015
MAX_CURVE_POINTS=4096

def arc_parameters(points):
    a,m,b=np.asarray(points,dtype=float)
    u=m-a;v=b-a;cross=u[0]*v[1]-u[1]*v[0]
    if abs(cross)<1e-8*max(np.linalg.norm(u)*np.linalg.norm(v),1e-12):
        raise ValueError('An arc needs three non-collinear points. Move the middle point off the line.')
    offset=np.array([np.dot(u,u)*v[1]-np.dot(v,v)*u[1],u[0]*np.dot(v,v)-v[0]*np.dot(u,u)])/(2*cross)
    center=a+offset;radius=float(np.linalg.norm(offset))
    angles=np.arctan2(np.asarray(points)[:,1]-center[1],np.asarray(points)[:,0]-center[0])
    middle=(angles[1]-angles[0])%(2*np.pi);end=(angles[2]-angles[0])%(2*np.pi)
    if middle<=end:sweeps=(middle,end-middle)
    else:sweeps=(middle-2*np.pi,end-middle)
    if not np.isfinite(radius) or radius>1e6:raise ValueError('Arc is too flat to construct reliably; spread its defining points.')
    return center,radius,float(angles[0]),sweeps

def circle_span(center,radius,start,sweep,a,b):
    # Sagitta gives a geometric chord-error bound; <=5° also avoids false dwell
    # corners on ordinary smooth circular tessellation.
    max_angle=min(np.pi/36,2*math.acos(max(-1.,1-CURVE_ERROR/max(radius,1e-12))))
    count=max(1,int(math.ceil(abs(sweep)/max(max_angle,1e-8))))
    if count+1>MAX_CURVE_POINTS:raise ValueError('Curve exceeds the output detail budget. Simplify or reduce its size.')
    theta=start+np.linspace(0,sweep,count+1)
    points=center+radius*np.column_stack((np.cos(theta),np.sin(theta)))
    points[0]=a;points[-1]=b
    return points

def bezier_polyline(control):
    """de Casteljau subdivision, with convex-hull/chord error and turn bounds."""
    output=[control[0]];pending=[(np.asarray(control,dtype=float),0)]
    while pending:
        p,depth=pending.pop();chord=p[3]-p[0];length=float(np.linalg.norm(chord))
        if length>1e-12:
            t=np.clip((p[1:3]-p[0])@chord/(length*length),0,1)
            error=float(np.linalg.norm(p[1:3]-(p[0]+t[:,None]*chord),axis=1).max())
            tangents=np.array([p[1]-p[0],p[3]-p[2]])
            norms=np.linalg.norm(tangents,axis=1);valid=norms>1e-12
            smooth=bool(np.all((tangents[valid]@chord)/(norms[valid]*length)>=math.cos(math.radians(10))))
        else:error=float(np.linalg.norm(p[1:3]-p[0],axis=1).max());smooth=True
        if error<=CURVE_ERROR and smooth:
            output.append(p[3])
        else:
            if depth>=16 or len(output)+len(pending)>=MAX_CURVE_POINTS-1:
                raise ValueError('Spline exceeds the output detail budget. Simplify or spread its points.')
            a=(p[0]+p[1])/2;b=(p[1]+p[2])/2;c=(p[2]+p[3])/2
            d=(a+b)/2;e=(b+c)/2;f=(d+e)/2
            pending.append((np.array([f,e,c,p[3]]),depth+1))
            pending.append((np.array([p[0],a,d,f]),depth+1))
    return np.asarray(output)

@lru_cache(maxsize=512)
def curve_spans(kind,coordinates,side=1):
    """A span per consecutive defining-node pair; exact knots remain vertices."""
    points=np.asarray(coordinates,dtype=float);spans=[]
    if kind=='Arc':
        center,radius,start,sweeps=arc_parameters(points)
        spans=[circle_span(center,radius,start,sweeps[0],points[0],points[1]),
               circle_span(center,radius,start+sweeps[0],sweeps[1],points[1],points[2])]
    elif kind=='Circle':
        center,edge=points;radius=float(np.linalg.norm(edge-center))
        start=float(np.arctan2(edge[1]-center[1],edge[0]-center[0]))
        spans=[circle_span(center,radius,start,2*np.pi,edge,edge)]
    elif kind=='Semicircle':
        a,b=points;center=(a+b)/2;radius=float(np.linalg.norm(b-a)/2)
        start=float(np.arctan2(a[1]-center[1],a[0]-center[0]))
        spans=[circle_span(center,radius,start,-side*np.pi,a,b)]
    elif kind=='Spline':
        closed=len(points)>3 and np.array_equal(points[0],points[-1])
        for i in range(len(points)-1):
            p1,p2=points[i:i+2]
            p0=points[i-1] if i else points[-2] if closed else 2*p1-p2
            p3=points[i+2] if i+2<len(points) else points[1] if closed else 2*p2-p1
            d0=np.sqrt(np.linalg.norm(p1-p0));d1=np.sqrt(np.linalg.norm(p2-p1));d2=np.sqrt(np.linalg.norm(p3-p2))
            if min(d0,d1,d2)<1e-8:raise ValueError('Spline points are too close together.')
            m1=(p2-p1)+d1*((p1-p0)/d0-(p2-p0)/(d0+d1))
            m2=(p2-p1)+d1*((p3-p2)/d2-(p3-p1)/(d1+d2))
            spans.append(bezier_polyline(np.array([p1,p1+m1/3,p2-m2/3,p2])))
    else:raise ValueError('Unsupported curve type.')
    if sum(len(s)-1 for s in spans)+1>MAX_CURVE_POINTS:raise ValueError('Curve exceeds the output detail budget. Use fewer points.')
    for span in spans:span.setflags(write=False)
    return tuple(spans)
