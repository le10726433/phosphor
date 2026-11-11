from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import numpy as np
from phosphor.engine.custom_shape import Drawing,Curve,CUSTOM_OBJECT
from phosphor.engine.drawing_motion import set_role,assign,reconcile,remove_assignment
from phosphor.engine.signal_engine import Shape,Settings,builder_motion_parts,render,validate
from phosphor.storage.app_files import scene_document,read_scene,atomic_json

class DrawingMotionTests(unittest.TestCase):
    def fixture(self,follow=False):
        d=Drawing('Assembly',((0,1,1),(1,3,1),(2,0,2),(3,.4,2),(4,.5,3),(5,.7,3)),((0,1),(2,3)),(Curve('Circle',(4,5)),))
        d=set_role(d,{0},True)
        return assign(d,{1,2},d.guides[0].id,1.,'Back and forth',follow,0.)

    def points(self,d,t,size=1.,rotation=0.):
        shape=Shape(solid=CUSTOM_OBJECT,custom=d,shape_size=size,rotation_z=rotation)
        return [p+c for p,c,r,e in builder_motion_parts(shape,0,t)]

    def test_assignment_has_no_position_jump_and_preserves_offset(self):
        for follow in (False,True):
            d=self.fixture(follow)
            before=self.points(replace(d,attachments=()),0)
            after=self.points(d,0)
            for a,b in zip(before,after):np.testing.assert_allclose(a,b,atol=1e-12)
            if not follow:
                still=replace(d,attachments=tuple(replace(b,speed=0.) for b in d.attachments))
                settings=replace(Settings(),shapes=(Shape(solid=CUSTOM_OBJECT,custom=still),))
                prior=replace(settings,shapes=(replace(settings.shapes[0],custom=replace(still,attachments=())),))
                times=np.arange(1200)/48000
                np.testing.assert_allclose(render(times,settings)[2],render(times,prior)[2],atol=1e-12)
            moved=self.points(d,1.)
            for a,b in zip(after,moved):np.testing.assert_allclose(b,a+[1,0],atol=1e-12)
            # Vertical guide starts do not rotate the follower at assignment.
            vertical=d.move(1,1,3);vertical=assign(vertical,{1,2},d.guides[0].id,1.,'Back and forth',True,0.)
            for a,b in zip(self.points(replace(vertical,attachments=()),0),self.points(vertical,0)):np.testing.assert_allclose(a,b,atol=1e-12)

    def test_scene_unit_velocity_with_scaled_parent_and_rigid_group(self):
        d=self.fixture();a=self.points(d,0,size=2,rotation=30);b=self.points(d,1,size=2,rotation=30)
        delta=np.array([np.cos(np.pi/6),np.sin(np.pi/6)])
        for p,q in zip(a,b):np.testing.assert_allclose(q-p,np.tile(delta,(len(p),1)),atol=1e-12)
        self.assertAlmostEqual(np.linalg.norm(b[1].mean(axis=0)-b[0].mean(axis=0)),np.linalg.norm(a[1].mean(axis=0)-a[0].mean(axis=0)))

    def test_guide_edits_and_whole_assembly_translation_do_not_double_offset(self):
        d=self.fixture();base=self.points(d,0)
        moved=d.translate({0,1,2},(.2,.3))
        for p,q in zip(base,self.points(moved,0)):np.testing.assert_allclose(q,p+[.2,.3],atol=1e-12)
        guide_only=d.translate({0},(.2,.3))
        for p,q in zip(base,self.points(guide_only,0)):np.testing.assert_allclose(q,p+[.2,.3],atol=1e-12)
        partial=d.translate({0,1},(.2,.3));self.assertEqual(len(partial.attachments),2)
        for p,q in zip(base,self.points(partial,0)):np.testing.assert_allclose(q,p+[.2,.3],atol=1e-12)
        removed=reconcile(d.without({0}));self.assertFalse(removed.guides);self.assertFalse(removed.attachments)

    def test_analytic_circle_follow_and_closed_loop(self):
        d=Drawing('Circle guide',((0,0,0),(1,1,0),(2,0,2),(3,.4,2)),((2,3),),(Curve('Circle',(0,1)),))
        d=set_role(d,{1},True);d=assign(d,{0},d.guides[0].id,1.,'Stop at end',True,0.)
        np.testing.assert_allclose(self.points(d,0)[0],[[0,2],[.4,2]],atol=1e-12)
        moving=self.points(d,1)[0];self.assertAlmostEqual(np.linalg.norm(np.diff(moving,axis=0)[0]),.4)
        self.assertFalse(np.allclose(moving,self.points(d,0)[0]))

    def test_scene_shape_clipboard_roundtrip_and_independent_remapping(self):
        d=self.fixture();self.assertEqual(Drawing.read(d.document()),d)
        copy=d.subset({0,1,2},keep_motion=True);pasted,selection=d.paste(copy,(.2,-.2))
        self.assertEqual(len(pasted.guides),2);self.assertNotEqual(pasted.guides[0].id,pasted.guides[1].id)
        pasted.validate();self.assertEqual(len(pasted.attachments),2)
        self.assertFalse(d.subset({1,2},keep_motion=True).attachments) # no dangling external guide in copied data
        settings=replace(Settings(),shapes=(Shape(solid=CUSTOM_OBJECT,custom=d),))
        document=scene_document(settings,'drawing','');self.assertEqual(document['version'],8)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'assembly.json';atomic_json(path,document);loaded,_,_=read_scene(path)
        self.assertEqual(loaded.shapes,settings.shapes)
        times=np.arange(7000)/48000;full=render(times,settings)[2]
        chunks=np.concatenate([render(t,settings)[2] for t in np.array_split(times,41)])
        np.testing.assert_allclose(chunks,full,atol=1e-12)
        only=set_role(Drawing('Guide',((0,0,0),(1,2,0)),((0,1),)),{0},True)
        settings=replace(settings,shapes=(replace(settings.shapes[0],custom=only),))
        np.testing.assert_array_equal(render(times,settings)[2],np.zeros((len(times),2)))

    def test_component_role_and_assignment_validation(self):
        d=self.fixture()
        with self.assertRaises(ValueError):assign(d,{0},d.guides[0].id,1.,'Back and forth',False,0.)
        with self.assertRaises(ValueError):set_role(d,{0,1},True)
        unmarked=set_role(d,{0},False);self.assertFalse(unmarked.guides[0].enabled);self.assertEqual(unmarked.attachments,d.attachments)
        self.assertEqual(set_role(unmarked,{0},True),d)
        two=set_role(d,{1},True);self.assertEqual(sum(g.enabled for g in two.guides),2)
        two=set_role(two,{0},False);self.assertTrue(two.guides[1].enabled)
        detached=remove_assignment(d,{1});self.assertEqual(set(detached.attachments[0].nodes),{4,5})
        with self.assertRaises(ValueError):replace(d,attachments=(replace(d.attachments[0],guide_id='missing'),)).validate()
        with self.assertRaises(ValueError):d.move(2,1,1,join=0)
