"""Pitch-preserving phase allocation for hardware corner settling."""
import numpy as np

MAX_DWELL_US = 500.
MAX_CYCLE_FRACTION = .25
MIN_TURN_DEGREES = 30.


def corner_positions(local, centers, cumulative):
    points = local+centers
    delta = np.diff(points,axis=0)
    lengths = np.linalg.norm(delta,axis=1)
    edges = np.flatnonzero(lengths>1e-12)
    if len(edges)<2 or cumulative[-1]<=0: return np.empty(0)
    directions = delta[edges]/lengths[edges,None]
    cosines = np.sum(directions*np.roll(directions,1,axis=0),axis=1)
    corners = edges[cosines <= np.cos(np.deg2rad(MIN_TURN_DEGREES))]
    return cumulative[corners]/cumulative[-1]


def dwell_phase(phase, frequency, corners, microseconds):
    """Reserve <=25% of each source cycle for equal holds at sharp turns.

    Frequency is supplied from the source/voice, never estimated from chunk edges.
    All integer source-phase boundaries remain the same, preserving cycle timing.
    """
    phase=np.mod(phase,1.)
    if microseconds<=0 or not len(corners): return phase
    frequency=np.broadcast_to(np.asarray(frequency,dtype=float),phase.shape)
    hold=np.minimum(np.maximum(frequency,0.)*microseconds*1e-6,MAX_CYCLE_FRACTION/len(corners))
    moving=1.-len(corners)*hold
    # Binary search each sample's completed holds; avoids samples × corners arrays.
    low=np.zeros(len(phase),dtype=np.int64);high=np.full(len(phase),len(corners),dtype=np.int64)
    while np.any(low<high):
        active=low<high;middle=(low+high)//2;index=np.minimum(middle,len(corners)-1)
        passed=phase>=corners[index]*moving+(index+1)*hold
        low=np.where(active&passed,middle+1,low)
        high=np.where(active&~passed,middle,high)
    index=np.minimum(low,len(corners)-1)
    start=corners[index]*moving+index*hold
    holding=(low<len(corners))&(phase>=start)
    result=np.where(holding,corners[index],(phase-low*hold)/moving)
    return np.clip(result,0.,np.nextafter(1.,0.))
