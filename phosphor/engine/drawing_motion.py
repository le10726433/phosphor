"""Immutable per-component guide roles and offset-preserving builder motion."""
from dataclasses import dataclass,replace,asdict
from functools import lru_cache
import math
import uuid
import numpy as np

@dataclass(frozen=True)
class Guide:
    id: str
    nodes: tuple
    name: str
    enabled: bool = True

@dataclass(frozen=True)
class Attachment:
    guide_id: str
    nodes: tuple
    reference: tuple
    heading: float
    speed: float = .25
    endpoint: str = 'Back and forth'
    follow: bool = False
    angle_offset: float = 0.

def metadata_document(values):return [asdict(v) for v in values]

def unique_name(base,names):
    if base not in names:return base
    n=2
    while f'{base[:58]} {n}' in names:n+=1
    return f'{base[:58]} {n}'

def read_guides(values):
    return tuple(Guide(**dict(v,nodes=tuple(v['nodes']))) for v in values)

def read_attachments(values):
    return tuple(Attachment(**dict(v,nodes=tuple(v['nodes']),reference=tuple(v['reference']))) for v in values)

def segment_indices(drawing,nodes):
    ids=set(nodes)
    return frozenset(k for k,segment in enumerate(drawing.segments) if ids.intersection(segment))

def expand_nodes(drawing,nodes):
    return tuple(sorted(drawing.node_ids(drawing.connected(segment_indices(drawing,nodes)))))

def validate_metadata(drawing):
    from phosphor.engine.motion_path import Motion
    if not isinstance(drawing.guides,tuple) or not isinstance(drawing.attachments,tuple):raise ValueError('Motion metadata must be immutable.')
    node_ids={n[0] for n in drawing.nodes};guide_nodes=set();ids=set();followers=set()
    for guide in drawing.guides:
        if not isinstance(guide,Guide) or not isinstance(guide.id,str) or not 1<=len(guide.id)<=64 or guide.id in ids:raise ValueError('Invalid or duplicate builder guide ID.')
        if not isinstance(guide.name,str) or not 1<=len(guide.name)<=64:raise ValueError('Invalid motion path name.')
        if type(guide.enabled) is not bool:raise ValueError('Invalid motion path role.')
        if not isinstance(guide.nodes,tuple) or not guide.nodes or any(type(i) is not int or i not in node_ids for i in guide.nodes):raise ValueError('Motion path references a missing point.')
        if len(set(guide.nodes))!=len(guide.nodes) or set(expand_nodes(drawing,guide.nodes))!=set(guide.nodes):raise ValueError('A motion path must contain a whole connected shape.')
        if guide.enabled and guide_nodes.intersection(guide.nodes):raise ValueError('Motion paths cannot be joined together. Detach them first.')
        if guide.enabled:guide_nodes.update(guide.nodes)
        ids.add(guide.id)
    for binding in drawing.attachments:
        if not isinstance(binding,Attachment) or binding.guide_id not in ids:raise ValueError('Assigned builder motion path is missing.')
        if not isinstance(binding.nodes,tuple) or not binding.nodes or any(type(i) is not int or i not in node_ids for i in binding.nodes):raise ValueError('Motion assignment references a missing shape.')
        if len(set(binding.nodes))!=len(binding.nodes) or set(expand_nodes(drawing,binding.nodes))!=set(binding.nodes):raise ValueError('Select whole connected shapes for motion.')
        if guide_nodes.intersection(binding.nodes):raise ValueError('A motion guide cannot also be a follower. Detach the geometry first.')
        if followers.intersection(binding.nodes):raise ValueError('A shape cannot follow two guides. Remove its old assignment before joining.')
        followers.update(binding.nodes)
        Motion(binding.guide_id,binding.speed,binding.endpoint,binding.follow,binding.angle_offset,binding.reference).validate()
        if type(binding.heading) not in (int,float) or not math.isfinite(binding.heading):raise ValueError('Invalid initial guide direction.')

def reconcile(drawing):
    """Track edits by node identity, without depending on shifting segment indices."""
    from phosphor.engine.stroke_style import reconcile_styles
    drawing=reconcile_styles(drawing)
    if not drawing.guides and not drawing.attachments:return drawing
    expanded=tuple(replace(g,nodes=expand_nodes(drawing,g.nodes)) for g in drawing.guides if segment_indices(drawing,g.nodes))
    guides=[];remap={}
    for g in expanded:
        existing=next((x for x in guides if not x.enabled and not g.enabled and set(x.nodes).intersection(g.nodes)),None)
        if existing is not None:remap[g.id]=existing.id
        else:guides.append(g)
    guides=tuple(guides)
    ids={g.id for g in guides}
    bindings=tuple(replace(b,guide_id=remap.get(b.guide_id,b.guide_id),nodes=expand_nodes(drawing,b.nodes)) for b in drawing.attachments if remap.get(b.guide_id,b.guide_id) in ids and segment_indices(drawing,b.nodes))
    return replace(drawing,guides=guides,attachments=bindings)

def validate_routes(drawing):
    from phosphor.engine.motion_path import guide_route
    for guide in drawing.guides:
        if guide.enabled:guide_route(drawing.subset(segment_indices(drawing,guide.nodes)))

def set_role(drawing,selection,enabled):
    from phosphor.engine.motion_path import guide_route
    drawing=reconcile(drawing)
    indices=drawing.connected(selection);nodes=drawing.node_ids(indices)
    if not indices:raise ValueError('Select a connected path first.')
    existing=[g for g in drawing.guides if nodes.intersection(g.nodes)]
    if enabled:
        guide_route(drawing.subset(indices))
        if len(existing)==1 and (not existing[0].enabled or set(existing[0].nodes)==nodes):
            bindings=tuple(replace(b,nodes=tuple(i for i in b.nodes if i not in nodes)) for b in drawing.attachments)
            return replace(drawing,motion_path=False,guides=tuple(replace(g,enabled=True,nodes=tuple(sorted(nodes))) if g.id==existing[0].id else g for g in drawing.guides),attachments=tuple(b for b in bindings if b.nodes))
        if existing:raise ValueError('Select one connected path at a time to mark it as a guide.')
        guide=Guide(uuid.uuid4().hex,tuple(sorted(nodes)),unique_name(f'Motion path {len(drawing.guides)+1}',{g.name for g in drawing.guides}))
        bindings=tuple(replace(b,nodes=tuple(i for i in b.nodes if i not in nodes)) for b in drawing.attachments)
        return replace(drawing,motion_path=False,guides=(*drawing.guides,guide),attachments=tuple(b for b in bindings if b.nodes))
    removed={g.id for g in existing}
    return replace(drawing,motion_path=False,guides=tuple(replace(g,enabled=False) if g.id in removed else g for g in drawing.guides))

def assign(drawing,selection,guide_id,speed,endpoint,follow,angle_offset):
    from phosphor.engine.motion_path import guide_route,sample_route,Motion
    from phosphor.engine.signal_engine import builder_local_tangents
    guide=next((g for g in drawing.guides if g.id==guide_id),None)
    if guide is None or not guide.enabled:raise ValueError('Choose an enabled motion path.')
    nodes=drawing.node_ids(drawing.connected(selection));all_guides={i for g in drawing.guides if g.enabled for i in g.nodes}
    if not nodes or nodes.intersection(all_guides):raise ValueError('Select follower shapes only, without their motion guide.')
    raw=drawing.subset(segment_indices(drawing,guide.nodes));points,closed=guide_route(raw)
    _,heading=sample_route(points,closed,0,Motion(guide_id),builder_local_tangents(raw))
    binding=Attachment(guide_id,tuple(sorted(nodes)),tuple(float(x) for x in points[0]),heading,speed,endpoint,follow,angle_offset)
    kept=tuple(replace(b,nodes=tuple(i for i in b.nodes if i not in nodes)) for b in drawing.attachments)
    result=replace(drawing,attachments=(*tuple(b for b in kept if b.nodes),binding));result.validate();return result

def remove_assignment(drawing,selection):
    nodes=drawing.node_ids(drawing.connected(selection))
    bindings=tuple(replace(b,nodes=tuple(i for i in b.nodes if i not in nodes)) for b in drawing.attachments)
    return replace(drawing,attachments=tuple(b for b in bindings if b.nodes))

@lru_cache(maxsize=256)
def render_components(drawing):
    """Compile rest geometry once. Only rigid transforms occur during playback."""
    from phosphor.engine.custom_shape import compiled
    remaining=set(range(len(drawing.segments)));result=[]
    enabled={g.id for g in drawing.guides if g.enabled}
    guide_nodes={i for g in drawing.guides if g.enabled for i in g.nodes}
    while remaining:
        indices=drawing.connected((min(remaining),));remaining-=indices;nodes=drawing.node_ids(indices)
        if nodes.intersection(guide_nodes):continue
        subset=drawing.subset(indices);vertices,edges,route,_=compiled(subset)
        binding=next((b for b in drawing.attachments if b.guide_id in enabled and nodes.intersection(b.nodes)),None)
        result.append((vertices,edges,route,binding))
    return tuple(result)

def shifted_bindings(drawing,moved,delta):
    guides={g.id:set(g.nodes) for g in drawing.guides}
    result=[]
    for b in drawing.attachments:
        translated=set(b.nodes)&moved
        if translated and guides[b.guide_id]<=moved:
            result.append(replace(b,nodes=tuple(i for i in b.nodes if i in translated),reference=tuple(float(x) for x in np.array(b.reference)+delta)))
            remaining=tuple(i for i in b.nodes if i not in translated)
            if remaining:result.append(replace(b,nodes=remaining))
        else:result.append(b)
    return tuple(result)
