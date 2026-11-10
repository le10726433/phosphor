from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import numpy as np
from signal_engine import Shape,Settings,planned_path,render,shape_audio
from scene_path import TRAVEL_MODES,plan_scene
from solids import geometry,route_for
from signal_preferences import DEFAULT_SIGNAL_PREFERENCES,validated_signal_preferences,read_signal_preferences
from app_files import scene_document,atomic_json
from custom_shape import Drawing,Curve,CUSTOM_OBJECT
from drawing_motion import set_role

class RedrawTests(unittest.TestCase):
    def test_single_shape_and_single_draw_are_exactly_unchanged(self):
        settings=replace(Settings(),shapes=(Shape('Square (2D)',rotation_seconds=None),))
        times=np.arange(8000)/48000
        for count in (1,3,32):
            np.testing.assert_array_equal(render(times,settings)[2],render(times,replace(settings,shape_redraw_count=count))[2])
        d=Drawing('Object and guide',((0,0,0),(1,1,0),(2,0,1),(3,1,1)),((0,1),(2,3)))
        d=set_role(d,{0},True)
        single=replace(settings,shapes=(Shape(solid=CUSTOM_OBJECT,custom=d,rotation_seconds=None),))
        np.testing.assert_array_equal(render(times,single)[2],render(times,replace(single,shape_redraw_count=4))[2])

    def test_order_is_all_draws_before_next_shape_with_one_travel(self):
        shapes=(Shape('Square (2D)',-2,0,.4,None),Shape('Triangle (2D)',2,0,.4,None))
        parts=[]
        from signal_engine import projected_solid
        for s in shapes:
            route=route_for(s.solid)
            parts.append((projected_solid(0,s)*s.shape_size,np.array([s.position_x,s.position_y]),np.r_[np.tile(route[:-1],3),route[-1]],geometry(s.solid)[1]))
        for mode in TRAVEL_MODES:
            expected,centers,travel=plan_scene(parts,mode)
            actual,actual_centers,cumulative=planned_path(0,60,shapes,mode,8,0.,3)
            np.testing.assert_array_equal(actual,expected);np.testing.assert_array_equal(actual_centers,centers)
            lengths=np.linalg.norm(np.diff(expected+centers,axis=0),axis=1)
            if mode=='Faster travel':lengths/=np.where(travel,8,1)
            np.testing.assert_allclose(cumulative,np.r_[0,np.cumsum(lengths)])
        complex=Drawing('Large circle',((0,0,0),(1,100,0)),(),(Curve('Circle',(0,1)),))
        large=Shape(solid=CUSTOM_OBJECT,custom=complex,rotation_seconds=None)
        with self.assertRaisesRegex(ValueError,'detail budget'):planned_path(0,60,(large,large),'No changes',8,0.,32)

    def test_chunk_timing_dwell_and_original_audio_unchanged(self):
        settings=replace(Settings(),shapes=(Shape('Square (2D)',-.5,0,.2,None),Shape('Triangle (2D)',.5,0,.2,None)),corner_dwell_us=50.,shape_redraw_count=3)
        times=np.arange(9000)/48000;full=render(times,settings)
        chunks=np.concatenate([render(t,settings)[2] for t in np.array_split(times,53)])
        np.testing.assert_allclose(chunks,full[2],atol=1e-12)
        normal=render(times,replace(settings,shape_redraw_count=1))
        np.testing.assert_array_equal(full[1],normal[1])
        self.assertEqual(scene_document(settings,'drawing',''),scene_document(replace(settings,shape_redraw_count=1),'drawing',''))
        phase=np.linspace(0,1,1000,endpoint=False)
        a=shape_audio(np.zeros(1000),phase,np.ones(1000),settings)
        b=shape_audio(np.zeros(1000),phase+1,np.ones(1000),settings)
        np.testing.assert_allclose(a,b,atol=1e-12)

    def test_preferences_migrate_validate_and_roundtrip(self):
        old=validated_signal_preferences(dict(corner_dwell_us=80.))
        self.assertEqual(old,dict(DEFAULT_SIGNAL_PREFERENCES,corner_dwell_us=80.))
        value=dict(old,shape_redraw=True,shape_redraw_count=4)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'signal.json';atomic_json(p,value);self.assertEqual(read_signal_preferences(p),value)
        for count in (0,33,True,2.5,float('nan')):
            with self.assertRaises(ValueError):validated_signal_preferences(dict(value,shape_redraw_count=count))
