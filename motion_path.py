"""Validated single-route guides and distance-based motion, without a timeline."""
from dataclasses import dataclass
from functools import lru_cache
import math
import numpy as np
from custom_shape import compiled

@dataclass(frozen=True)
class Motion:
    guide_id: str
    speed: float = .25
    endpoint: str = 'Back and forth'
    follow: bool = False
    angle_offset: float = 0.
    anchor: tuple = (0.,0.)

    def validate(self):
        if not isinstance(self.guide_id,str) or not self.guide_id or len(self.guide_id)>64:raise ValueError('Motion guide reference is missing.')
        if type(self.speed) not in (int,float) or not math.isfinite(self.speed) or not 0<=self.speed<=100:raise ValueError('Velocity must be between 0 and 100 scene units per second.')
        if self.endpoint not in ('Back and forth','Stop at end') or type(self.follow) is not bool:raise ValueError('Unsupported motion behavior.')
        if type(self.angle_offset) not in (int,float) or not math.isfinite(self.angle_offset) or not -360<=self.angle_offset<=360:raise ValueError('Angle offset must be between -360 and 360 degrees.')
        if not isinstance(self.anchor,tuple) or len(self.anchor)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in self.anchor):raise ValueError('Invalid motion group anchor.')
        return self

    @classmethod
    def read(cls,value):
        if not isinstance(value,dict) or set(value)!={'guide_id','speed','endpoint','follow','angle_offset','anchor'}:raise ValueError('Unsupported motion data.')
        try:return cls(**dict(value,anchor=tuple(value['anchor']))).validate()
        except TypeError as error:raise ValueError('Malformed motion data.') from error

@lru_cache(maxsize=256)
def guide_route(drawing):
    vertices,edges,_,travel=compiled(drawing)
    if travel:raise ValueError('This path is disconnected. Edit the shape to make one continuous path before using it for motion.')
    adjacency=[[] for _ in vertices]
    for k,(a,b) in enumerate(edges):adjacency[a].append((b,k));adjacency[b].append((a,k))
    if any(len(a)>2 for a in adjacency):raise ValueError('This path branches. Edit the shape to remove branches before using it for motion.')
    ends=[i for i,a in enumerate(adjacency) if len(a)==1]
    closed=not ends
    if len(ends) not in (0,2):raise ValueError('Edit the shape into one open path or closed loop before using it for motion.')
    start=ends[0] if ends else 0;route=[start];used=set();current=start
    while len(used)<len(edges):
        option=next(((n,k) for n,k in adjacency[current] if k not in used),None)
        if option is None:raise ValueError('Edit the shape to make one continuous motion path.')
        current,k=option;used.add(k);route.append(current)
    points=vertices[np.asarray(route),:2].copy();points.setflags(write=False)
    return points,closed

def sample_route(points,closed,seconds,motion,tangents=None):
    lengths=np.linalg.norm(np.diff(points,axis=0),axis=1);cumulative=np.r_[0.,np.cumsum(lengths)];total=cumulative[-1]
    if total<1e-10:raise ValueError('Motion path has no usable length.')
    distance=max(0.,float(seconds))*motion.speed;direction=1.
    if closed:distance%=total
    elif motion.endpoint=='Back and forth':
        distance%=2*total
        if distance>=total:distance=2*total-distance;direction=-1.
    else:distance=min(distance,total)
    index=min(len(lengths)-1,max(0,int(np.searchsorted(cumulative,distance,side='right')-1)))
    fraction=(distance-cumulative[index])/max(lengths[index],1e-12)
    delta=points[index+1]-points[index]
    position=points[index]+fraction*delta
    tangent=delta if tangents is None else tangents[index,0]*(1-fraction)+tangents[index,1]*fraction
    angle=math.atan2(tangent[1]*direction,tangent[0]*direction)+math.radians(motion.angle_offset)
    return position,angle
