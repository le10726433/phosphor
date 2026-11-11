import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
import numpy as np
from phosphor.engine.custom_shape import Drawing,compiled,CUSTOM_OBJECT,MAX_NODES
from phosphor.engine.signal_engine import Shape,Settings,render,planned_path
from phosphor.storage.app_files import read_scene,scene_document,atomic_json
from phosphor.engine.scene_path import TRAVEL_MODES

class CustomShapeTests(unittest.TestCase):
    def setUp(self):
        self.outline=Drawing('Triangle',((10,-.6,-.4),(20,.6,-.4),(30,0,.6)),((10,20),(20,30),(30,10)))

    def test_closed_outline_once_and_open_branches_retrace_only_edges(self):
        vertices,edges,route,travel=compiled(self.outline)
        self.assertEqual(len(route),4);self.assertEqual(route[0],route[-1]);self.assertFalse(travel)
        branch=replace(self.outline,nodes=(*self.outline.nodes,(40,0,.9)),edges=(*self.outline.edges,(30,40)))
        vertices,edges,route,_=compiled(branch)
        allowed={tuple(sorted(e)) for e in edges}
        traversed={tuple(sorted((a,b))) for a,b in zip(route,route[1:])}
        self.assertEqual(allowed,traversed);self.assertEqual(route[0],route[-1])
        self.assertLessEqual(len(route),2*len(edges)+1)

    def test_components_have_explicit_travel_and_connected_routes(self):
        graph=Drawing('Separate',((0,-1,0),(1,-.5,0),(2,.5,0),(3,1,0)),((0,1),(2,3)))
        _,edges,route,travel=compiled(graph)
        self.assertEqual(travel,((0,2),));self.assertEqual(route[0],route[-1])
        self.assertIn((0,2),map(tuple,edges))
        settings=replace(Settings(),shapes=(Shape(solid=CUSTOM_OBJECT,custom=graph),Shape()))
        for mode in TRAVEL_MODES:
            settings=replace(settings,travel_mode=mode)
            self.assertTrue(np.isfinite(render(np.arange(512)/48000,settings)[2]).all())

    def test_join_detach_and_immutable_snapshot(self):
        original=self.outline
        detached,node=original.detach(20,0)
        self.assertEqual(original,self.outline)
        self.assertNotEqual(node,20);self.assertIn(node,detached.edges[0]);self.assertIn(20,detached.edges[1])
        moved=detached.move(node,.3,-.7)
        self.assertEqual(original.nodes[1],(20,.6,-.4))
        joined=moved.move(node,.6,-.4,join=20)
        self.assertEqual(joined,original)
        deleted=original.delete(20)
        self.assertEqual(deleted.edges,((30,10),))

    def test_scene_roundtrip_and_render_chunk_independence(self):
        settings=replace(Settings(),shapes=(Shape(solid=CUSTOM_OBJECT,custom=self.outline),),corner_dwell_us=80.)
        times=np.arange(6000)/48000
        full=render(times,settings)[2]
        pieces=np.concatenate([render(t,settings)[2] for t in np.array_split(times,47)])
        np.testing.assert_allclose(full,pieces,atol=1e-12)
        document=scene_document(settings,'drawing','')
        self.assertEqual(document['version'],8)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'custom.scene.json';atomic_json(path,document);loaded,_,_=read_scene(path)
        self.assertEqual(loaded.shapes,settings.shapes)
        # Hardware calibration is deliberately not in scene files.
        np.testing.assert_array_equal(render(times,replace(loaded,corner_dwell_us=80.))[2],full)
        self.assertEqual(scene_document(Settings(),'mixed','')['version'],8)
        self.assertNotIn('custom',scene_document(Settings(),'mixed','')['shapes'][0])

    def test_reject_bad_graphs_and_bound_geometry(self):
        invalid=[replace(self.outline,nodes=((0,float('nan'),0),)),
            replace(self.outline,edges=((10,10),)),replace(self.outline,edges=((10,99),)),
            replace(self.outline,edges=((10,20),(20,10))),
            Drawing('Too many',tuple((i,i,0) for i in range(MAX_NODES+1)),()),
            Drawing('Zero',((0,0,0),(1,0,0)),((0,1),))]
        for graph in invalid:
            with self.subTest(graph=graph),self.assertRaises(ValueError):graph.validate()
        for value in (None,{},dict(name='Bad',nodes=[None],edges=[])):
            with self.assertRaises(ValueError):Drawing.read(value)
        with self.assertRaises(ValueError):compiled(Drawing())

if __name__=='__main__':unittest.main()
