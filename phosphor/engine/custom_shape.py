"""Immutable line/curve graphs and bounded deterministic stereo traversal."""
from dataclasses import dataclass,replace
from functools import lru_cache
import math
import uuid
import numpy as np
from phosphor.engine.curve_geometry import curve_spans,arc_parameters,MAX_CURVE_POINTS
from phosphor.engine.drawing_motion import Guide,Attachment,metadata_document,read_guides,read_attachments,validate_metadata,shifted_bindings,reconcile,unique_name
from phosphor.engine.stroke_style import Style,validate_styles,bold_outline,corner_indices
from phosphor.engine.vector_text import GLYPHS,validate_text

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
    styles: tuple = ()  # Style: bold outline width / extrude depth per connected shape

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
        if not isinstance(self.styles,tuple):raise ValueError('Drawing snapshots must use immutable tuples.')
        if self.styles:validate_styles(self)
        return self

    def document(self):
        value=dict(name=self.name,nodes=[list(n) for n in self.nodes],edges=[list(e) for e in self.edges])
        if self.curves:value['curves']=[c.document() for c in self.curves]
        if self.motion_path:value['motion_path']=True
        if self.guides:value['guides']=metadata_document(self.guides)
        if self.attachments:value['attachments']=metadata_document(self.attachments)
        if self.styles:value['styles']=[s.document() for s in self.styles]
        return value

    @classmethod
    def read(cls,value):
        try:
            if not isinstance(value,dict) or not {'name','nodes','edges'}<=set(value)<={'name','nodes','edges','curves','motion_path','guides','attachments','styles'}: raise ValueError('Unsupported custom shape data.')
            if not isinstance(value.get('styles',[]),list):raise ValueError('Unsupported stroke style data.')
            return cls(value['name'],tuple(tuple(n) for n in value['nodes']),tuple(tuple(e) for e in value['edges']),tuple(Curve.read(c) for c in value.get('curves',())),value.get('motion_path',False),read_guides(value.get('guides',())),read_attachments(value.get('attachments',())),tuple(Style.read(v) for v in value.get('styles',()))).validate()
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
        styles=tuple(replace(s,nodes=tuple(i for i in s.nodes if i in used)) for s in self.styles)
        result=replace(result,styles=tuple(s for s in styles if s.nodes))
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
        result=replace(result,guides=(*self.guides,*guides),attachments=(*self.attachments,*bindings),
            styles=(*self.styles,*(replace(s,nodes=tuple(mapping[i] for i in s.nodes if i in mapping)) for s in other.styles)))
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

def text_drawing(text,height=.3,name=None):
    """Text as ordinary builder strokes, centred on the origin, letters `height` tall.

    Uses the app's own stroke alphabet (vector_text.GLYPHS, a 4x6 grid with 6-unit
    advance). Points shared within a letter become one node, so its strokes join.
    """
    validate_text(text)
    if not text.strip():raise ValueError('Type the text to place first.')
    if not 0<height<=50:raise ValueError('Text size must be above 0 and at most 50.')
    scale=height/6;nodes=[];edges=[];keys=set()
    for column,char in enumerate(text.upper()):
        if char==' ':continue
        lookup={}
        for stroke in GLYPHS[char].split('|'):
            ids=[]
            for pair in stroke.split():
                point=(int(pair[0])+column*6,int(pair[1]))
                if point not in lookup:lookup[point]=len(nodes);nodes.append((len(nodes),float(point[0]),float(point[1])))
                ids.append(lookup[point])
            for a,b in zip(ids,ids[1:]):
                if a!=b and tuple(sorted((a,b))) not in keys:edges.append((a,b));keys.add(tuple(sorted((a,b))))
    if len(nodes)>MAX_NODES:raise ValueError(f'That text needs {len(nodes)} nodes; a shape holds {MAX_NODES}. Use fewer characters.')
    xs=[n[1] for n in nodes];ys=[n[2] for n in nodes];cx=(min(xs)+max(xs))/2;cy=(min(ys)+max(ys))/2
    nodes=tuple((i,(x-cx)*scale,(y-cy)*scale) for i,x,y in nodes)
    if max(max(abs(x),abs(y)) for _,x,y in nodes)>90:raise ValueError('That text is too large for the drawing area. Use a smaller size or fewer characters.')
    return Drawing((name or text.strip())[:64],nodes,tuple(edges)).validate(playable=True)

@lru_cache(maxsize=64)
def bold_preview(drawing):
    """(segment indices drawn as bold, 2D outline polylines) for the builder canvas."""
    lines=paths(drawing);hidden=set();pieces=[]
    for style in drawing.styles:
        if not style.bold:continue
        indices=[k for k,segment in enumerate(drawing.segments) if set(style.nodes).intersection(segment)]
        hidden.update(indices);pieces.extend(bold_outline([lines[k] for k in indices],style.bold))
    return frozenset(hidden),tuple(pieces)

def _styled(drawing,vertices,edges,index):
    """Apply bold outlines and Z extrusion to the styled shapes' part of the graph.

    edges are built in drawing.segments order, so the edge's segment is recoverable:
    plain lines first, then each curve's tessellated chain.
    """
    positions={i:(x,y) for i,x,y in drawing.nodes}
    segment_of=list(range(len(drawing.edges)))
    for k,curve in enumerate(drawing.curves,len(drawing.edges)):
        spans=curve_spans(curve.kind,tuple(positions[i] for i in curve.nodes),curve.side)
        segment_of.extend([k]*sum(len(p)-1 for p in spans))
    lines=paths(drawing);vertices=[tuple(v) for v in vertices];edges=list(edges)
    style_of_segment={}
    for style in drawing.styles:
        for k,segment in enumerate(drawing.segments):
            if set(style.nodes).intersection(segment):style_of_segment[k]=style
    tagged={}  # vertex -> style, for extrusion
    keep=[]
    for e,(a,b) in enumerate(edges):
        style=style_of_segment.get(segment_of[e])
        if style is not None and style.bold:continue  # Replaced by the outline below.
        keep.append((a,b))
        if style is not None:tagged[a]=tagged[b]=style
    edges=keep;corners=set()
    for style in drawing.styles:
        if style.depth:corners.update(index[i] for i in style.nodes if i in index and not style.bold)
        if not style.bold:continue
        outline=bold_outline([lines[k] for k,s in style_of_segment.items() if s is style],style.bold)
        ends=[];join=style.bold*.05  # Pieces cut at the same crossing (within the 2% inside tolerance) share one vertex.
        def endpoint(point):
            for v in ends:
                if math.dist(vertices[v][:2],point)<join:return v
            vertices.append((float(point[0]),float(point[1]),0.));ends.append(len(vertices)-1);return len(vertices)-1
        for piece in outline:
            closed=len(piece)>2 and np.linalg.norm(piece[0]-piece[-1])<1e-9
            body=piece[:-1] if closed else piece
            if closed:
                first=len(vertices);vertices.extend((float(x),float(y),0.) for x,y in body);ids=list(range(first,len(vertices)))
            else:
                start=endpoint(body[0]);first=len(vertices)
                vertices.extend((float(x),float(y),0.) for x,y in body[1:-1])
                ids=[start,*range(first,len(vertices)),endpoint(body[-1])]
            for v in ids:tagged[v]=style
            edges.extend((a,b) for a,b in zip(ids,ids[1:]) if a!=b)
            if closed:edges.append((ids[-1],ids[0]))
            if style.depth:corners.update(ids[i] for i in corner_indices(piece) if i<len(ids))
    if any(s.depth for s in drawing.styles):
        # Front copy at +depth/2, back copy at -depth/2, joined at the shape's corners.
        back={}
        for v,style in tagged.items():
            if not style.depth:continue
            x,y,_=vertices[v];half=style.depth/2
            vertices[v]=(x,y,half);back[v]=len(vertices);vertices.append((x,y,-half))
        edges.extend((back[a],back[b]) for a,b in list(edges) if a in back and b in back)
        edges.extend((v,back[v]) for v in sorted(corners) if v in back)
    used=sorted({i for e in edges for i in e})
    remap={v:k for k,v in enumerate(used)}
    return [vertices[v] for v in used],[(remap[a],remap[b]) for a,b in edges]

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
    if drawing.styles:vertices,edges=_styled(drawing,vertices,edges,index)
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
