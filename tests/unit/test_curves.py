"""Curve definitions, joins, deterministic tessellation and audio integration."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import numpy as np
from phosphor.engine.custom_shape import Drawing,Curve,compiled,paths,CUSTOM_OBJECT
from phosphor.engine.curve_geometry import CURVE_ERROR,curve_spans
from phosphor.engine.signal_engine import Settings,Shape,render,planned_path
from phosphor.engine.corner_dwell import corner_positions
from phosphor.storage.app_files import scene_document,read_scene,atomic_json

class CurveTests(unittest.TestCase):
    def arc(self):return Drawing('Arc',((0,-1,0),(1,0,1),(2,1,0)),(),(Curve('Arc',(0,1,2)),))

    def test_arc_circle_and_through_point_and_reversed_direction(self):
        d=self.arc();p=paths(d)[0]
        np.testing.assert_allclose(np.linalg.norm(p,axis=1),1,atol=1e-12)
        self.assertGreaterEqual(p[:,1].min(),-1e-12)
        for point in ((-1,0),(0,1),(1,0)):self.assertTrue(np.any(np.all(p==point,axis=1)))
        reverse=replace(d,curves=(Curve('Arc',(2,1,0)),))
        np.testing.assert_allclose(paths(reverse)[0],p[::-1],atol=1e-12)
        mid=(p[:-1]+p[1:])/2
        self.assertLessEqual(np.max(1-np.linalg.norm(mid,axis=1)),CURVE_ERROR)
        major=Drawing('Major',((0,1,0),(1,-1,0),(2,0,1)),(),(Curve('Arc',(0,1,2)),))
        self.assertLess(paths(major)[0][:,1].min(),-.99)

    def test_semicircle_flip_and_joined_full_circle_has_no_false_dwell(self):
        d=Drawing('Semi',((0,-1,0),(1,1,0)),(),(Curve('Semicircle',(0,1)),))
        p=paths(d)[0];flipped=d.flip(0);q=paths(flipped)[0]
        np.testing.assert_allclose(q,p*[1,-1],atol=1e-12)
        circle=replace(d,curves=(*d.curves,*flipped.curves))
        v,e,route,_=compiled(circle)
        self.assertEqual(len(route),len(e)+1)
        local,centers,cumulative=planned_path(0,60,(Shape(solid=CUSTOM_OBJECT,custom=circle),),'No changes',8)
        self.assertEqual(len(corner_positions(local,centers,cumulative)),0)

    def test_spline_interpolates_knots_and_matches_independent_uniform_formula(self):
        pts=((-1,0),(0,1),(1,0),(2,1))
        spans=curve_spans('Spline',pts)
        for index,span in enumerate(spans):
            np.testing.assert_array_equal(span[0],pts[index]);np.testing.assert_array_equal(span[-1],pts[index+1])
        t=np.linspace(0,1,1001)[:,None];a,b,c,d=np.array(pts)
        exact=.5*(2*b+(-a+c)*t+(2*a-5*b+4*c-d)*t*t+(-a+3*b-3*c+d)*t*t*t)
        poly=spans[1];starts=poly[:-1];delta=np.diff(poly,axis=0)
        factors=np.clip(np.sum((exact[:,None]-starts)*delta,axis=2)/np.sum(delta*delta,axis=1),0,1)
        distances=np.linalg.norm(exact[:,None]-(starts+factors[:,:,None]*delta),axis=2).min(axis=1)
        self.assertLessEqual(distances.max(),CURVE_ERROR)
        uneven=curve_spans('Spline',((0,0),(.001,.001),(.2,.8),(1,0)))
        self.assertTrue(all(np.isfinite(p).all() for p in uneven))
        closed=curve_spans('Spline',((0,0),(1,0),(0,1),(0,0)))
        np.testing.assert_array_equal(closed[0][0],closed[-1][-1])

    def test_curve_control_moves_detach_join_delete_and_snapshot_immutability(self):
        d=self.arc();moved=d.move(1,0,.6)
        self.assertNotEqual(paths(d)[0].shape,paths(moved)[0].shape)
        self.assertEqual(d.nodes[1],(1,0,1))
        joined=replace(d,nodes=(*d.nodes,(3,-1,-1)),edges=((0,3),))
        detached,node=joined.detach(0,1)
        self.assertNotEqual(node,0);self.assertIn(0,detached.edges[0]);self.assertEqual(detached.curves[0].nodes[0],node)
        self.assertEqual(detached.move(node,-1,0,join=0),joined)
        spline=replace(d,curves=(Curve('Spline',(0,1,2)),))
        self.assertEqual(spline.delete(1).curves,(Curve('Spline',(0,2)),))
        self.assertFalse(d.delete(1).curves)
        self.assertFalse(d.remove_segment(0).curves)
        with self.assertRaises(ValueError):d.move(1,0,0)

    def test_curves_roundtrip_analytic_data_and_chunk_independent_audio(self):
        nodes=((0,-1,0),(1,0,.7),(2,1,0),(3,0,-.6))
        drawing=Drawing('Mixed',nodes,((0,3),),(Curve('Arc',(0,1,2)),Curve('Semicircle',(2,3),-1),Curve('Spline',(3,1,2))))
        settings=replace(Settings(),shapes=(Shape(solid=CUSTOM_OBJECT,custom=drawing),),corner_dwell_us=40.)
        document=scene_document(settings,'drawing','')
        self.assertEqual(document['version'],10)
        self.assertEqual(Drawing.read(drawing.document()),drawing)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'curves.scene.json';atomic_json(p,document);loaded,_,_=read_scene(p)
        self.assertEqual(loaded.shapes,settings.shapes)
        times=np.arange(7000)/48000
        full=render(times,settings)[2]
        chunked=np.concatenate([render(t,settings)[2] for t in np.array_split(times,57)])
        np.testing.assert_allclose(chunked,full,atol=1e-12)
        self.assertLess(len(document['shapes'][0]['custom']['nodes']),10)
        # The old three-field line drawing still loads unchanged.
        old=dict(name='Old line',nodes=[[0,0,0],[1,1,0]],edges=[[0,1]])
        self.assertFalse(Drawing.read(old).curves)
        self.assertEqual(Drawing.read(old).document(),old)

    def test_bad_curves_and_detail_budget_fail_before_playback(self):
        d=self.arc()
        bad=[replace(d,curves=(Curve('Arc',(0,1)),)),replace(d,curves=(Curve('Bogus',(0,2)),)),
             replace(d,curves=(Curve('Semicircle',(0,99)),)),replace(d,curves=(Curve('Semicircle',(0,2),0),)),
             replace(d,curves=(Curve('Spline',(0,0,2)),)),replace(d,curves=(Curve('Spline',(0,2,0)),)),
             replace(d,nodes=((0,-1,0),(1,0,0),(2,1,0)))]
        for graph in bad:
            with self.subTest(graph=graph),self.assertRaises(ValueError):graph.validate()
        huge=replace(d,nodes=((0,-100,0),(1,0,100),(2,100,0)),curves=(Curve('Arc',(0,1,2)),)*20)
        with self.assertRaises(ValueError):compiled(huge)
        settings=replace(Settings(),shapes=(Shape(solid=CUSTOM_OBJECT,custom=huge),))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'excessive.scene.json';atomic_json(path,scene_document(settings,'drawing',''))
            with self.assertRaises(ValueError):read_scene(path)

if __name__=='__main__':unittest.main()
