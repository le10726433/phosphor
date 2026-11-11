"""Small original stroke alphabet for continuous X-Y text trajectories.

Glyph coordinates are authored here, not extracted from a third-party font.
Disconnected strokes and characters require visible travel: stereo has no blanking.
"""
from functools import lru_cache
import numpy as np

TEXT_OBJECT = 'Text'
DEFAULT_TEXT = 'TEXT'
MAX_TEXT_LENGTH = 64
# Each pair is an x/y point on a 4-by-6 grid; | begins a separate pen stroke.
GLYPHS = {
 'A':'00 03 26 43 40|03 43', 'B':'00 06 36 45 44 33 03|33 42 41 30 00',
 'C':'46 16 05 01 10 40', 'D':'00 06 26 45 41 20 00',
 'E':'46 06 00 40|03 33', 'F':'00 03 06 46|03 33',
 'G':'46 16 05 01 10 40 43 23', 'H':'00 03 06|40 43 46|03 43',
 'I':'06 26 46|26 20|00 20 40', 'J':'06 46 41 30 10 01',
 'K':'00 03 06|46 03 40', 'L':'06 00 40',
 'M':'00 06 23 46 40', 'N':'00 06 40 46',
 'O':'10 01 05 16 36 45 41 30 10', 'P':'00 03 06 36 45 44 33 03',
 'Q':'10 01 05 16 36 45 41 30 10|22 40', 'R':'00 03 06 36 45 44 33 03|23 40',
 'S':'46 16 05 04 32 41 30 00', 'T':'06 26 46|26 20',
 'U':'06 01 10 30 41 46', 'V':'06 20 46', 'W':'06 10 23 30 46',
 'X':'06 23 40|00 23 46', 'Y':'06 23 46|23 20', 'Z':'06 46 00 40',
 '0':'10 01 05 16 36 45 41 30 10|01 45', '1':'14 26 20|00 20 40',
 '2':'05 16 36 45 44 00 40', '3':'06 36 45 34 23 34 42 41 30 00',
 '4':'36 03 43|33 30', '5':'46 06 03 33 42 41 30 00',
 '6':'46 16 05 01 10 30 41 42 33 03', '7':'06 46 10',
 '8':'13 04 05 16 36 45 44 33 13 02 01 10 30 41 42 33',
 '9':'43 13 04 05 16 36 45 41 30 00',
 '.':'20 21', ',':'20 11', ':':'21 22|24 25', ';':'20 11|24 25',
 '!':'20 21|23 26', '?':'05 16 36 45 44 23 22|20 21',
 '-':'03 43', '_':'00 40', '+':'03 23 43|20 23 26',
 '=':'02 42|04 44', '/':'00 46', '\\':'06 40',
 '(':'36 15 11 30', ')':'16 35 31 10', '[':'36 16 10 30', ']':'16 36 30 10',
 '{':'36 25 24 13 22 21 30', '}':'16 25 24 33 22 21 10',
 '<':'45 03 41', '>':'05 43 01', '"':'16 14|36 34', "'":'26 24',
 '#':'10 16|30 36|02 42|04 44', '*':'03 43|15 31|11 35',
 '%':'00 46|05 16 25 14 05|21 32 41 30 21',
 '&':'40 14 15 26 35 34 01 10 43', '@':'40 10 01 05 16 36 45 41 31 34 14 11 31',
 '$':'45 16 05 04 32 41 30 01|20 26', '^':'04 26 44', '`':'16 25', '|':'20 26',
 '~':'02 13 32 43',
}


def validate_text(text):
    if not isinstance(text,str) or len(text)>MAX_TEXT_LENGTH:
        raise ValueError(f'Text must contain at most {MAX_TEXT_LENGTH} characters.')
    if any(c != ' ' and c.upper() not in GLYPHS for c in text):
        raise ValueError('Use letters A–Z, numbers, spaces and standard punctuation.')
    return text


@lru_cache(maxsize=128)
def text_geometry(text):
    return text_route(text)[:3]


def text_route(text):
    """Vertices, edges, closed route, and the (a, b) edges that only join separate strokes."""
    validate_text(text)
    vertices,edges,lookup = [],[],{}
    def vertex(point):
        if point not in lookup:
            lookup[point]=len(vertices);vertices.append(point)
        return lookup[point]
    for index,char in enumerate(text.upper()):
        if char == ' ': continue
        for stroke in GLYPHS[char].split('|'):
            indices=[vertex((int(pair[0])+index*6,int(pair[1]),0)) for pair in stroke.split()]
            edges.extend((a,b) for a,b in zip(indices,indices[1:]) if a!=b)
    if not vertices:
        # Blank text is a zero-length trajectory, not a substituted word.
        return np.zeros((1,3)),[(0,0)],np.array([0,0]),np.empty((0,2),dtype=int)
    points=np.asarray(vertices,dtype=float)
    # Join disconnected strokes by shortest visible travel, then retrace edges.
    drawn=len(edges)
    parent=list(range(len(points)))
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for a,b in edges:parent[root(a)]=root(b)
    while True:
        connected=[i for i in range(len(points)) if root(i)==root(0)]
        remaining=[i for i in range(len(points)) if root(i)!=root(0)]
        if not remaining:break
        distances=np.linalg.norm(points[connected,None]-points[None,remaining],axis=2)
        a,b=np.unravel_index(np.argmin(distances),distances.shape)
        a,b=connected[a],remaining[b];edges.append((a,b));parent[root(b)]=root(a)
    points-=(points.min(axis=0)+points.max(axis=0))/2
    radius=np.linalg.norm(points,axis=1).max()
    if radius:points/=radius
    adjacency=[[] for _ in points]
    for eid,(a,b) in enumerate(edges+edges):
        adjacency[a].append((b,eid));adjacency[b].append((a,eid))
    used,stack,route=set(),[0],[]
    while stack:
        a=stack[-1]
        while adjacency[a] and adjacency[a][-1][1] in used:adjacency[a].pop()
        if adjacency[a]:
            b,eid=adjacency[a].pop();used.add(eid);stack.append(b)
        else:route.append(stack.pop())
    return points,edges,np.array(route[::-1]),np.array(edges[drawn:],dtype=int).reshape(-1,2)
