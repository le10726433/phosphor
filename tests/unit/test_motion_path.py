"""Motion route validation, rigid poses, phase timing and portable scenes."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import numpy as np
from phosphor.engine.custom_shape import Drawing,Curve,compiled,paths,CUSTOM_OBJECT
from phosphor.engine.motion_path import Motion,guide_route,sample_route
from phosphor.engine.signal_engine import Shape,Settings,posed_shapes,render,validate
from phosphor.storage.app_files import scene_document,read_scene,atomic_json
from phosphor.editor.builder_selection import dashed_lines

class MotionTests(unittest.TestCase):
    def line(self):return Drawing('Guide',((0,0,0),(1,2,0)),((0,1),),motion_path=True)
    def guide(self):return Shape(solid=CUSTOM_OBJECT,custom=self.line(),uid='guide')

    def test_open_velocity_bounce_stop_and_restart(self):
        points,closed=guide_route(self.line());m=Motion('guide',1.)
        for time,x in ((0,0),(.5,.5),(2,2),(2.5,1.5),(4,0),(4.5,.5)):
            position,angle=sample_route(points,closed,time,m);np.testing.assert_allclose(position,(x,0))
        self.assertAlmostEqual(abs(sample_route(points,closed,2.5,m)[1]),np.pi)
        stopped=replace(m,endpoint='Stop at end')
        np.testing.assert_array_equal(sample_route(points,closed,999,stopped)[0],(2,0))
        np.testing.assert_array_equal(sample_route(points,closed,0,stopped)[0],(0,0))

    def test_circle_center_radius_closed_route_and_continuous_loop(self):
        d=Drawing('Circle',((0,0,0),(1,1,0)),(),(Curve('Circle',(0,1)),),True)
        points,closed=guide_route(d);self.assertTrue(closed)
        np.testing.assert_allclose(np.linalg.norm(points,axis=1),1,atol=1e-12)
        np.testing.assert_array_equal(points[0],points[-1]);self.assertEqual(len(compiled(d)[0]),len(points)-1)
        length=np.linalg.norm(np.diff(points,axis=0),axis=1).sum();m=Motion('g',1.,endpoint='Stop at end')
        np.testing.assert_allclose(sample_route(points,closed,.2,m)[0],sample_route(points,closed,length+.2,m)[0],atol=1e-12)
        self.assertEqual(Drawing.read(d.document()),d)
        guide=Shape(solid=CUSTOM_OBJECT,custom=d,uid='g')
        following=Shape(motion=Motion('g',follow=True))
        self.assertAlmostEqual(posed_shapes((following,guide),0)[0].rotation_z,90.)

    def test_branched_and_disconnected_routes_require_editing(self):
        branch=Drawing('Branch',((0,0,0),(1,1,0),(2,0,1),(3,0,-1)),((0,1),(0,2),(0,3)))
        with self.assertRaisesRegex(ValueError,'branches.*Edit'):guide_route(branch)
        separate=replace(branch,edges=((0,1),(2,3)))
        with self.assertRaisesRegex(ValueError,'disconnected.*Edit'):guide_route(separate)

    def test_rigid_group_and_direction_offset_reversal(self):
        guide=self.guide();m=Motion('guide',1.,anchor=(0.,0.))
        a=Shape(position_x=-.25,motion=m)
        b=Shape(position_x=.25,motion=m)
        poses=posed_shapes((a,b,guide),.5)
        self.assertEqual(len(poses),2);self.assertAlmostEqual(poses[1].position_x-poses[0].position_x,.5)
        self.assertAlmostEqual((poses[0].position_x+poses[1].position_x)/2,.5)
        follow=replace(m,follow=True,angle_offset=90.)
        poses=posed_shapes((replace(a,motion=follow),replace(b,motion=follow),guide),.5)
        self.assertAlmostEqual(poses[1].position_y-poses[0].position_y,.5)
        self.assertAlmostEqual(poses[0].rotation_z,90)
        poses=posed_shapes((replace(a,motion=replace(m,follow=True)),guide),2.5)
        self.assertAlmostEqual(abs(poses[0].rotation_z),180)
        paused=replace(guide,custom=replace(guide.custom,motion_path=False))
        self.assertEqual(posed_shapes((a,paused),.5)[0],a)

    def test_motion_samples_chunk_consistent_guide_exclusion_and_files(self):
        settings=replace(Settings(),shapes=(Shape(motion=Motion('guide',.3)),self.guide()))
        times=np.arange(8000)/48000
        full=render(times,settings)[2]
        chunks=np.concatenate([render(t,settings)[2] for t in np.array_split(times,43)])
        np.testing.assert_allclose(chunks,full,atol=1e-12)
        document=scene_document(settings,'drawing','');self.assertEqual(document['version'],8)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'motion.json';atomic_json(p,document);loaded,_,_=read_scene(p)
        self.assertEqual(loaded.shapes,settings.shapes);np.testing.assert_array_equal(render(times,loaded)[2],full)
        only=replace(settings,shapes=(self.guide(),));np.testing.assert_array_equal(render(times,only)[2],np.zeros((len(times),2)))
        with self.assertRaisesRegex(ValueError,'missing'):validate(replace(settings,shapes=(settings.shapes[0],)))

    def test_dashed_marquee_geometry(self):
        lines=dashed_lines(np.array([[0.,0.],[20.,0.],[20.,20.]]))
        self.assertTrue(lines);self.assertTrue(all(np.linalg.norm(b-a)<=6+1e-9 for a,b in lines))
        np.testing.assert_array_equal(lines[0],((0,0),(6,0)))
        np.testing.assert_array_equal(lines[1],((10,0),(16,0)))

if __name__=='__main__':unittest.main()
