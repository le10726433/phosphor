"""Immutable line/curve graphs and bounded deterministic stereo traversal."""
from dataclasses import dataclass,replace
from functools import lru_cache
import math
import uuid
import numpy as np
from phosphor.engine.curve_geometry import curve_spans,arc_parameters,MAX_CURVE_POINTS
from phosphor.engine.drawing_motion import Guide,Attachment,metadata_document,read_guides,read_attachments,validate_metadata,shifted_bindings,reconcile,unique_name

CUSTOM_OBJECT = 'Custom shape'
MAX_NODES, MAX_EDGES = 128, 256

@dataclass(frozen=True)
class Curve:
    kind: str
    nodes: tuple
    side: int = 1

    def document(self):return dict(kind=self.kind,nodes=list(self.nodes),side=self.side)

    @classmethod
    def read(cls,value):
        if not isinstance(value,dict) or set(value)!={'kind','nodes','side'}:raise ValueError('Unsupported curve data.')
        return cls(value['kind'],tuple(value['nodes']),value['side'])

@dataclass(frozen=True)
class Drawing:
    name: str = 'My shape'
    nodes: tuple = ()  # (stable integer ID, x, y)
    edges: tuple = ()  # shared endpoint IDs; crossings do not create joins
    curves: tuple = ()  # defining nodes, not derived tessellation
    motion_path: bool = False
    guides: tuple = ()
    attachments: tuple = ()

    @property
    def playable(self):return bool(self.edges or self.curves)

    @property
    def segments(self):return (*self.edges,*(c.nodes for c in self.curves))

    def validate(self, playable=False):
        if type(self.motion_path) is not bool:raise ValueError('Motion path role must be enabled or disabled.')
        if not isinstance(self.nodes,tuple) or not isinstance(self.edges,tuple) or not isinstance(self.curves,tuple) or any(not isinstance(v,tuple) for v in (*self.nodes,*self.edges)):
            raise ValueError('Drawing snapshots must use immutable tuples.')
        if not isinstance(self.name,str) or not 1<=len(self.name.strip())<=64:
            raise ValueError('Give the shape a name (1–64 characters).')
        if len(self.nodes)>MAX_NODES or len(self.edges)+len(self.curves)>MAX_EDGES:
            raise ValueError(f'Limit: {MAX_NODES} nodes and {MAX_EDGES} segments.')
        ids=set()
        for node in self.nodes:
            if len(node)!=3 or type(node[0]) is not int or not 0<=node[0]<=1000000 or node[0] in ids:
                raise ValueError('Invalid or duplicate node ID.')
            ids.add(node[0])
            if any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>100 for x in node[1:]):
                raise ValueError('Node coordinates must be finite and within ±100.')
        seen=set();positions={n[0]:n[1:] for n in self.nodes}
        for edge in self.edges:
            if len(edge)!=2 or any(type(i) is not int or i not in ids for i in edge) or edge[0]==edge[1]:
                raise ValueError('A line needs two different existing endpoints.')
            key=tuple(sorted(edge))
            if key in seen: raise ValueError('Duplicate line.')
            seen.add(key)
            if math.dist(positions[edge[0]],positions[edge[1]])<1e-8:
                raise ValueError('Line endpoints are too close together.')
        for curve in self.curves:
            if not isinstance(curve,Curve) or not isinstance(curve.nodes,tuple) or curve.kind not in ('Arc','Semicircle','Spline','Circle') or type(curve.side) is not int or curve.side not in (-1,1):
                raise ValueError('Invalid curve definition.')
            expected={'Arc':3,'Semicircle':2,'Circle':2}.get(curve.kind)
            if (expected is not None and len(curve.nodes)!=expected) or (curve.kind=='Spline' and not 2<=len(curve.nodes)<=MAX_NODES+1):
                raise ValueError('Curve has an invalid number of defining points.')
            if any(type(i) is not int or i not in ids for i in curve.nodes):raise ValueError('Curve references a missing node.')
            if curve.kind in ('Arc','Semicircle','Circle') and len(set(curve.nodes))!=len(curve.nodes):raise ValueError('Curve defining points must be different.')
            for a,b in zip(curve.nodes,curve.nodes[1:]):
                if math.dist(positions[a],positions[b])<1e-8:raise ValueError('Curve points are too close together.')
            if curve.kind=='Spline' and curve.nodes[0]==curve.nodes[-1] and len(curve.nodes)<4:
                raise ValueError('A closed spline needs at least three different points.')
            if curve.kind=='Arc':arc_parameters([positions[i] for i in curve.nodes])
        if playable and not self.playable: raise ValueError('Draw at least one segment, then finish the stroke.')
        if self.guides or self.attachments:validate_metadata(self)
        return self

    def document(self):
        value=dict(name=self.name,nodes=[list(n) for n in self.nodes],edges=[list(e) for e in self.edges])
        if self.curves:value['curves']=[c.document() for c in self.curves]
        if self.motion_path:value['motion_path']=True
        if self.guides:value['guides']=metadata_document(self.guides)
        if self.attachments:value['attachments']=metadata_document(self.attachments)
        return value

    @classmethod
    def read(cls,value):
        try:
            if not isinstance(value,dict) or not {'name','nodes','edges'}<=set(value)<={'name','nodes','edges','curves','motion_path','guides','attachments'}: raise ValueError('Unsupported custom shape data.')
            return cls(value['name'],tuple(tuple(n) for n in value['nodes']),tuple(tuple(e) for e in value['edges']),tuple(Curve.read(c) for c in value.get('curves',())),value.get('motion_path',False),read_guides(value.get('guides',())),read_attachments(value.get('attachments',()))).validate()
        except (TypeError,KeyError) as error: raise ValueError('Malformed custom shape data.') from error

    def move(self,node,x,y,join=None):
        if join is not None and join!=node:
            edges=[]
            for a,b in self.edges:
                a=join if a==node else a;b=join if b==node else b
                if a!=b and tuple(sorted((a,b))) not in {tuple(sorted(e)) for e in edges}:edges.append((a,b))
            curves=tuple(replace(c,nodes=tuple(join if i==node else i for i in c.nodes)) for c in self.curves)
            guides=tuple(replace(g,nodes=tuple(sorted({join if i==node else i for i in g.nodes}))) for g in self.guides)
            bindings=tuple(replace(b,nodes=tuple(sorted({join if i==node else i for i in b.nodes}))) for b in self.attachments)
            return reconcile(replace(self,nodes=tuple(n for n in self.nodes if n[0]!=node),edges=tuple(edges),curves=curves,guides=guides,attachments=bindings)).validate()
        return replace(self,nodes=tuple((i,float(x),float(y)) if i==node else (i,a,b) for i,a,b in self.nodes)).validate()

    def delete(self,node):
        curves=[]
        for c in self.curves:
            if node not in c.nodes:curves.append(c)
            elif c.kind=='Spline':
                closed=c.nodes[0]==c.nodes[-1]
                body=c.nodes[:-1] if closed else c.nodes
                body=tuple(i for i in body if i!=node)
                if len(body)>=(3 if closed else 2):curves.append(replace(c,nodes=(*body,body[0]) if closed else body))
        return replace(self,nodes=tuple(n for n in self.nodes if n[0]!=node),edges=tuple(e for e in self.edges if node not in e),curves=tuple(curves))

    def detach(self,node,edge_index):
        edge=self.segments[edge_index]
        if node not in edge: raise ValueError('Select an incident line.')
        fresh=max((n[0] for n in self.nodes),default=-1)+1
        position=next(n[1:] for n in self.nodes if n[0]==node)
        edges=list(self.edges);curves=list(self.curves)
        if edge_index<len(edges):edges[edge_index]=tuple(fresh if i==node else i for i in edge)
        else:
            index=edge_index-len(edges);curves[index]=replace(curves[index],nodes=tuple(fresh if i==node else i for i in edge))
        return reconcile(replace(self,nodes=(*self.nodes,(fresh,*position)),edges=tuple(edges),curves=tuple(curves))).validate(),fresh

    def remove_segment(self,index):
        if index<len(self.edges):return replace(self,edges=tuple(e for k,e in enumerate(self.edges) if k!=index))
        return replace(self,curves=tuple(c for k,c in enumerate(self.curves) if k!=index-len(self.edges)))

    def connected(self,indices):
        chosen=set(indices);nodes={i for k in chosen for i in self.segments[k]}
        while True:
            more={k for k,segment in enumerate(self.segments) if nodes.intersection(segment)}
            if more<=chosen:return frozenset(chosen)
            chosen|=more;nodes.update(i for k in more for i in self.segments[k])

    def node_ids(self,indices):return {i for k in indices for i in self.segments[k]}

    def translate(self,indices,delta):
        moved=self.node_ids(self.connected(indices));dx,dy=delta
        return replace(self,nodes=tuple((i,float(x+dx),float(y+dy)) if i in moved else (i,x,y) for i,x,y in self.nodes),attachments=shifted_bindings(self,moved,np.array(delta))).validate()

    def subset(self,indices,keep_motion=False):
        chosen=set(indices);used=self.node_ids(chosen)
        result=Drawing(self.name,tuple(n for n in self.nodes if n[0] in used),
            tuple(e for k,e in enumerate(self.edges) if k in chosen),
            tuple(c for k,c in enumerate(self.curves,len(self.edges)) if k in chosen))
        if keep_motion:
            guides=tuple(g for g in self.guides if set(g.nodes)<=used);ids={g.id for g in guides}
            bindings=tuple(replace(b,nodes=tuple(i for i in b.nodes if i in used)) for b in self.attachments if b.guide_id in ids and used.intersection(b.nodes))
            result=replace(result,guides=guides,attachments=bindings)
        return result

    def without(self,indices):
        chosen=set(indices);removed=self.node_ids(chosen)
        kept=set(range(len(self.segments)))-chosen;used=self.node_ids(kept)
        return replace(self,nodes=tuple(n for n in self.nodes if n[0] not in removed or n[0] in used),
            edges=tuple(e for k,e in enumerate(self.edges) if k not in chosen),
            curves=tuple(c for k,c in enumerate(self.curves,len(self.edges)) if k not in chosen))

    def paste(self,other,delta):
        other.validate(playable=True);fresh=max((n[0] for n in self.nodes),default=-1)+1
        mapping={n[0]:fresh+k for k,n in enumerate(other.nodes)};dx,dy=delta
        result=replace(self,nodes=(*self.nodes,*((mapping[i],float(x+dx),float(y+dy)) for i,x,y in other.nodes)),
            edges=(*self.edges,*((mapping[a],mapping[b]) for a,b in other.edges)),
            curves=(*self.curves,*(replace(c,nodes=tuple(mapping[i] for i in c.nodes)) for c in other.curves)))
        guide_map={g.id:uuid.uuid4().hex for g in other.guides}
        guides=[];names={g.name for g in self.guides}
        for g in other.guides:
            name=unique_name(g.name[:58]+' copy',names);names.add(name)
            guides.append(replace(g,id=guide_map[g.id],nodes=tuple(mapping[i] for i in g.nodes),name=name))
        bindings=tuple(replace(b,guide_id=guide_map[b.guide_id],nodes=tuple(mapping[i] for i in b.nodes),reference=tuple(float(x) for x in np.array(b.reference)+delta)) for b in other.attachments)
        result=replace(result,guides=(*self.guides,*guides),attachments=(*self.attachments,*bindings))
        selected=frozenset((*range(len(self.edges),len(result.edges)),*range(len(result.edges)+len(self.curves),len(result.segments))))
        return result.validate(),selected

    def flip(self,index):
        index-=len(self.edges)
        if not 0<=index<len(self.curves) or self.curves[index].kind!='Semicircle':raise ValueError('Select a semicircle to flip.')
        curves=list(self.curves);curves[index]=replace(curves[index],side=-curves[index].side)
        return replace(self,curves=tuple(curves))

@lru_cache(maxsize=256)
def paths(drawing):
    drawing.validate();nodes={i:(x,y) for i,x,y in drawing.nodes}
    result=[np.array([nodes[a],nodes[b]]) for a,b in drawing.edges]
    count=len(result)
    for curve in drawing.curves:
        spans=curve_spans(curve.kind,tuple(nodes[i] for i in curve.nodes),curve.side)
        count+=sum(len(s)-1 for s in spans)
        if count>MAX_CURVE_POINTS:raise ValueError('Drawing exceeds the output detail budget. Simplify its curves.')
        result.append(np.concatenate((spans[0],*(s[1:] for s in spans[1:]))))
    for p in result:p.setflags(write=False)
    return tuple(result)

@lru_cache(maxsize=256)
def compiled(drawing):
    """Return vertices, connected traversal edges, route and visible travel links.

    Eulerian components traverse each edge once. Others retrace edges using a
    bounded iterative depth-first walk. Disconnected components use explicit
    straight return trips; stereo cannot blank these moves.
    """
    drawing.validate(playable=True)
    used={i for e in drawing.edges for i in e}|{i for c in drawing.curves for i in (c.nodes[1:] if c.kind=='Circle' else c.nodes)}
    nodes=[n for n in drawing.nodes if n[0] in used]
    index={n[0]:i for i,n in enumerate(nodes)}
    vertices=[(x,y,0.) for _,x,y in nodes]
    edges=[(index[a],index[b]) for a,b in drawing.edges]
    positions={i:(x,y) for i,x,y in drawing.nodes}
    paths(drawing) # validates the global tessellation budget before allocating
    for curve in drawing.curves:
        spans=curve_spans(curve.kind,tuple(positions[i] for i in curve.nodes),curve.side)
        pairs=((curve.nodes[1],curve.nodes[1]),) if curve.kind=='Circle' else zip(curve.nodes,curve.nodes[1:])
        for (a,b),points in zip(pairs,spans):
            previous=index[a]
            for x,y in points[1:-1]:
                current=len(vertices);vertices.append((x,y,0.));edges.append((previous,current));previous=current
            edges.append((previous,index[b]))
    vertices=np.array(vertices,dtype=float)
    adjacency=[[] for _ in vertices]
    for k,(a,b) in enumerate(edges):adjacency[a].append((b,k));adjacency[b].append((a,k))
    unseen=set(range(len(vertices)));components=[]
    while unseen:
        seed=min(unseen);component=set();pending=[seed]
        while pending:
            v=pending.pop()
            if v in component:continue
            component.add(v);pending.extend(n for n,_ in adjacency[v] if n not in component)
        unseen-=component;components.append(sorted(component))
    route=[];travel=[]
    for component in components:
        root=component[0];visited=set()
        if all(len(adjacency[i])%2==0 for i in component):
            stack=[root];walk=[]
            while stack:
                v=stack[-1];option=next(((n,k) for n,k in adjacency[v] if k not in visited),None)
                if option is None:walk.append(stack.pop())
                else:visited.add(option[1]);stack.append(option[0])
            walk=walk[::-1]
        else:
            walk=[root];stack=[(root,iter(adjacency[root]))]
            while stack:
                v,neighbors=stack[-1];option=next(((n,k) for n,k in neighbors if k not in visited),None)
                if option is None:
                    stack.pop()
                    if stack:walk.append(stack[-1][0])
                else:
                    n,k=option;visited.add(k);walk.append(n);stack.append((n,iter(adjacency[n])))
        if route:
            anchor=route[0];travel.append((anchor,root));edges.append((anchor,root))
            route.extend(walk);route.append(anchor)
        else:route.extend(walk)
    result=(vertices,np.asarray(edges,dtype=int),np.asarray(route,dtype=int),tuple(travel))
    for array in result[:3]:array.setflags(write=False)
    return result
