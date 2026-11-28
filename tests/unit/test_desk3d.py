"""Unit tests for the Late-80s 3D Photorealistic Oscilloscope Mode (QtQuick3D)."""
import argparse
import os
import unittest

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from phosphor.display.phosphor import QUALITY_HIGH, QUALITY_LOW, QUALITY_MEDIUM, QUALITY_ULTRA
from phosphor.editor.desk3d import (
    CAMERA_PRESETS, LIGHTING_MODES, compute_crt_light_metrics,
)
from phosphor.editor.desk3d_assets import (
    AbsPlasticAlbedoTexture, AbsPlasticNormalTexture, ChassisRoughnessTexture,
    ControlEmissiveTexture, ControlFaceplateTexture, CrtGlassOverlayTexture,
    LabDeskAlbedoTexture, LabDeskNormalTexture, LabEnvironmentProbeTexture,
    SideVentTexture, TEXTURE_ROOT, TEXTURE_TIERS, build_bail_handle_mesh,
    build_bnc_jack_mesh, build_bnc_plug_collar_mesh, build_crt_hood_mesh,
    build_curved_crt_mesh, build_faceplate_screw_mesh, build_keycap_mesh,
    build_knurled_knob_mesh, build_rounded_bezel_frame_mesh,
    build_strain_boot_mesh, texture_tier_for_quality,
)
from phosphor.editor.desk3d_props import (
    BobbleheadFaceTexture, BobbleheadSpring, CalStickerTexture, CoaxCableGeometry,
    ContactShadowTexture, CrtGraticuleGlassTexture, CuttingMatNormalTexture,
    CuttingMatRoughnessTexture, CuttingMatTexture, LabWallChartTexture,
    NotepadPageTexture, VerletCoaxCable, build_coffee_mug_mesh,
    build_helical_spring_mesh, build_hex_pencil_barrel_mesh,
    build_hollow_lamp_shade_mesh, build_lamp_power_cord_mesh,
    build_lamp_shade_inner_mesh, build_lamp_volumetric_cone_mesh,
    build_mug_handle_mesh, build_spare_probe_cord_mesh,
)
from phosphor.editor.qt_app import Window


def _app():
    return QApplication.instance() or QApplication([])


class Desk3DAssetsTests(unittest.TestCase):
    def test_procedural_3d_meshes_have_valid_geometry_and_unit_normals(self):
        for fn in (build_curved_crt_mesh, build_rounded_bezel_frame_mesh, build_crt_hood_mesh,
                   build_knurled_knob_mesh, build_keycap_mesh, build_bnc_jack_mesh,
                   build_bnc_plug_collar_mesh, build_strain_boot_mesh, build_bail_handle_mesh,
                   build_faceplate_screw_mesh, build_hollow_lamp_shade_mesh,
                   build_lamp_shade_inner_mesh, build_lamp_volumetric_cone_mesh,
                   build_helical_spring_mesh, build_coffee_mug_mesh, build_mug_handle_mesh,
                   build_hex_pencil_barrel_mesh, build_spare_probe_cord_mesh,
                   build_lamp_power_cord_mesh):
            pos, nrm, uv, idx = fn()
            self.assertGreater(len(pos), 12)
            self.assertEqual(pos.shape[1], 3)
            self.assertEqual(nrm.shape, pos.shape)
            self.assertEqual(uv.shape, (len(pos), 2))
            self.assertEqual(len(idx) % 3, 0)
            self.assertTrue(np.all(np.isfinite(pos)))
            self.assertTrue(np.all(np.isfinite(nrm)))
            lengths = np.linalg.norm(nrm, axis=1)
            np.testing.assert_allclose(lengths, 1.0, atol=1e-4)
            self.assertLess(int(idx.max()), len(pos))

        # Verify outward triangle winding aligns with vertex normals on all swept/lathed props
        for fn in (build_hollow_lamp_shade_mesh, build_lamp_shade_inner_mesh,
                   build_strain_boot_mesh, build_bail_handle_mesh, build_faceplate_screw_mesh,
                   build_helical_spring_mesh, build_coffee_mug_mesh, build_mug_handle_mesh,
                   build_spare_probe_cord_mesh, build_lamp_power_cord_mesh):
            pos, nrm, _, idx = fn()
            tris = idx.reshape(-1, 3)
            p0, p1, p2 = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
            tri_cross = np.cross(p1 - p0, p2 - p0)
            avg_n = nrm[tris[:, 0]] + nrm[tris[:, 1]] + nrm[tris[:, 2]]
            dots = np.sum(tri_cross * avg_n, axis=1)
            self.assertGreater(float(np.mean(dots)), 0.0, f'Inverted triangle winding in {fn.__name__}')

    def test_curved_crt_mesh_bulges_forward_at_center(self):
        pos, _, _, _ = build_curved_crt_mesh(width=174.0, height=174.0, bulge=10.5, segs=16)
        r = np.hypot(pos[:, 0], pos[:, 1])
        z_center = float(pos[np.argmin(r), 2])
        z_corner = float(pos[np.argmax(r), 2])
        self.assertGreater(z_center, z_corner + 5.0)

    def test_bundled_cc0_pbr_textures_and_hdri_exist_for_1k_and_2k(self):
        self.assertTrue((TEXTURE_ROOT / 'LICENSE.txt').is_file())
        expected_files = (
            'chassis_albedo.jpg', 'chassis_normal.jpg', 'chassis_roughness.jpg',
            'aluminum_albedo.jpg', 'aluminum_normal.jpg', 'aluminum_roughness.jpg',
            'desk_albedo.jpg', 'desk_normal.jpg', 'desk_roughness.jpg',
            'wall_albedo.jpg', 'wall_normal.jpg', 'wall_roughness.jpg',
            'lab_env.hdr',
        )
        for tier in TEXTURE_TIERS:
            for fname in expected_files:
                path = TEXTURE_ROOT / tier / fname
                self.assertTrue(path.is_file(), f'Missing {tier}/{fname}')
                self.assertGreater(path.stat().st_size, 10_000, f'Suspiciously small {tier}/{fname}')

    def test_lofted_bezel_meshes_use_continuous_arc_length_uvs(self):
        for fn in (build_rounded_bezel_frame_mesh, build_crt_hood_mesh):
            _, _, uv, _ = fn()
            self.assertGreater(float(uv[:, 0].max()), 2.0)

    def test_quality_tier_mapping_and_faceplate_resolution_switching(self):
        _app()
        self.assertEqual(texture_tier_for_quality(QUALITY_LOW), '1k')
        self.assertEqual(texture_tier_for_quality(QUALITY_MEDIUM), '1k')
        self.assertEqual(texture_tier_for_quality(QUALITY_HIGH), '2k')
        self.assertEqual(texture_tier_for_quality(QUALITY_ULTRA), '2k')

        fp = ControlFaceplateTexture(tier='1k')
        self.assertEqual((fp.size().width(), fp.size().height()), (1024, 512))
        fp.set_tier('2k')
        self.assertEqual((fp.size().width(), fp.size().height()), (2048, 1024))

        em = ControlEmissiveTexture(tier='1k')
        self.assertEqual((em.size().width(), em.size().height()), (1024, 512))
        em.set_tier('2k')
        self.assertEqual((em.size().width(), em.size().height()), (2048, 1024))

    def test_procedural_pbr_and_prop_textures_populate_rgba8_buffers(self):
        _app()
        for cls, expected_size in (
            (LabEnvironmentProbeTexture, (512, 256)),
            (AbsPlasticAlbedoTexture, (512, 512)),
            (AbsPlasticNormalTexture, (512, 512)),
            (ChassisRoughnessTexture, (512, 512)),
            (SideVentTexture, (512, 256)),
            (CrtGlassOverlayTexture, (512, 512)),
            (CrtGraticuleGlassTexture, (512, 512)),
            (CuttingMatTexture, (1024, 512)),
            (CuttingMatNormalTexture, (1024, 512)),
            (CuttingMatRoughnessTexture, (1024, 512)),
            (ContactShadowTexture, (256, 256)),
            (LabWallChartTexture, (512, 384)),
            (CalStickerTexture, (512, 256)),
            (BobbleheadFaceTexture, (512, 512)),
            (NotepadPageTexture, (768, 960)),
            (LabDeskAlbedoTexture, (512, 512)),
            (LabDeskNormalTexture, (512, 512)),
            (ControlFaceplateTexture, (2048, 1024)),
            (ControlEmissiveTexture, (2048, 1024)),
        ):
            tex = cls()
            self.assertEqual((tex.size().width(), tex.size().height()), expected_size)
            self.assertEqual(len(tex.textureData()), expected_size[0] * expected_size[1] * 4)
        self.assertTrue(CrtGraticuleGlassTexture().hasTransparency())
        self.assertTrue(CalStickerTexture().hasTransparency())

        # Verify all 5 tattered notepad pages render distinct RGBA buffers
        pad = NotepadPageTexture(page=0)
        page_hashes = set()
        for idx in range(NotepadPageTexture.PAGE_COUNT):
            pad.set_page(idx)
            page_hashes.add(bytes(pad.textureData())[:4096])
        self.assertEqual(len(page_hashes), 5)

    def test_verlet_coax_cable_and_bobblehead_physics(self):
        _app()
        sockets = ((46.0, -73.6, 24.7), (102.0, -73.6, 24.7))
        cable = VerletCoaxCable(sockets, 0, (24.0, -126.0, 175.0))
        geom = CoaxCableGeometry(cable)
        self.assertTrue(cable.plugged)
        # Pre-settled plugged cable starts sleeping (zero idle wiggle)
        self.assertTrue(cable.sleeping)
        self.assertFalse(cable.step(1.0 / 60.0))
        cable.grab()
        # Small wiggle stays plugged
        self.assertEqual(cable.move_grab((52.0, -70.0, 32.0)), 'tugging')
        self.assertTrue(cable.plugged)
        # Pulling past UNPLUG_DIST unplugs with recoil
        self.assertEqual(cable.move_grab((110.0, -40.0, 70.0)), 'unplugged')
        self.assertFalse(cable.plugged)
        cable.release()
        for _ in range(45):
            cable.step(1.0 / 60.0)
        geom.sync_mesh()
        self.assertTrue(np.all(np.isfinite(cable.pos)))
        self.assertGreaterEqual(float(cable.pos[:, 1].min()), VerletCoaxCable.DESK_Y - 1e-3)
        # Dragging near port 1 snaps into port 1 (cross-patching!)
        cable.grab()
        cable.move_grab((100.0, -75.0, 26.0))
        status, port = cable.release(occupied_ports=())
        self.assertEqual((status, port), ('replugged', 1))
        self.assertEqual(cable.port_idx, 1)

        # Bobblehead damped spring wobbles on poke and decays cleanly
        spring = BobbleheadSpring()
        self.assertFalse(spring.is_moving)
        spring.poke()
        self.assertTrue(spring.is_moving)
        spring.step(1.0 / 60.0)
        self.assertGreater(abs(spring.rx) + abs(spring.rz), 0.5)
        for _ in range(300):
            spring.step(1.0 / 60.0)
        self.assertLess(abs(spring.rx) + abs(spring.rz), 1.5)

    def test_reactive_crt_bezel_light_tracks_silence_and_trace_position(self):
        e0, cx0, cy0, l0, r0, t0, b0 = compute_crt_light_metrics(np.zeros((256, 2), dtype=np.float32))
        self.assertEqual(e0, 0.0)
        self.assertEqual((l0, r0, t0, b0), (0.0, 0.0, 0.0, 0.0))

        ang = np.linspace(0.0, 2.0 * np.pi, 256, dtype=np.float32)
        sig_right = np.column_stack([0.75 + 0.15 * np.cos(ang), 0.10 * np.sin(ang)])
        er, cxr, cyr, lr, rr, tr, br = compute_crt_light_metrics(sig_right, beam_scale=1.0)
        self.assertGreater(er, 0.25)
        self.assertGreater(cxr, 0.5)
        self.assertGreater(rr, lr * 4.0)

        sig_tl = np.column_stack([-0.75 + 0.15 * np.cos(ang), 0.70 + 0.15 * np.sin(ang)])
        et, cxt, cyt, lt, rt, tt, bt = compute_crt_light_metrics(sig_tl, beam_scale=1.0)
        self.assertGreater(et, 0.25)
        self.assertLess(cxt, -0.5)
        self.assertGreater(cyt, 0.5)
        self.assertGreater(lt, rt * 4.0)
        self.assertGreater(tt, bt * 4.0)


class Desk3DIntegrationTests(unittest.TestCase):
    def test_enter_interact_and_exit_3d_lab_bench_mode(self):
        app = _app()
        opts = argparse.Namespace(scene=None, ready_file=None, workspace='Sandbox', skip_recovery=True)
        win = Window(opts)
        try:
            win.set_desk3d(True)
            app.processEvents()
            self.assertTrue(win.desk3d)
            self.assertIsNotNone(win.desk3d_host)
            self.assertEqual(win.desk3d_host.component.errors(), [])

            ctrl = win.desk3d_host.controller
            # Quality-coupled 1K/2K texture tier switching
            win.apply_preferences(dict(win.preferences, graphics_quality=QUALITY_LOW))
            self.assertEqual(ctrl.textureTier, '1k')
            self.assertTrue(ctrl.textureBaseUrl.endswith('/1k/'))
            self.assertEqual(win.desk3d_host._assets['faceplateAlbedoData'].size().width(), 1024)
            win.apply_preferences(dict(win.preferences, graphics_quality=QUALITY_ULTRA))
            self.assertEqual(ctrl.textureTier, '2k')
            self.assertTrue(ctrl.textureBaseUrl.endswith('/2k/'))
            self.assertEqual(win.desk3d_host._assets['faceplateAlbedoData'].size().width(), 2048)

            # True glass-thickness graticule parallax: phosphor shader hides flat grid while cover-glass renders it
            crt_p = ctrl.crt_prefs(win.preferences)
            self.assertFalse(crt_p['show_grid'])

            # Real-time signal-driven CRT bezel lighting
            for _ in range(25):
                ctrl.update_crt_light(np.zeros((256, 2), dtype=np.float32), elapsed=0.05)
            self.assertLess(ctrl.crtEnergy, 0.05)
            ang = np.linspace(0.0, 2.0 * np.pi, 256, dtype=np.float32)
            sig_right = np.column_stack([0.8 + 0.1 * np.cos(ang), 0.1 * np.sin(ang)])
            for _ in range(15):
                ctrl.update_crt_light(sig_right, elapsed=0.05)
            self.assertGreater(ctrl.crtEnergy, 0.15)
            self.assertGreater(ctrl.crtCentroidX, 0.5)
            self.assertGreater(ctrl.crtRightBounce, ctrl.crtLeftBounce * 3.0)

            # Physical fluorescent pushbutton & architect desk lamp base switch
            self.assertFalse(ctrl.fluorescentOn)
            self.assertTrue(ctrl.deskLampOn)
            self.assertEqual(ctrl.lightingMode, 1)
            ctrl.pressButton('btn_fluorescent')
            self.assertTrue(ctrl.fluorescentOn)
            self.assertEqual(ctrl.lightingMode, 0)
            ctrl.pressButton('btn_fluorescent')
            ctrl.pressButton('btn_lamp')
            self.assertFalse(ctrl.deskLampOn)
            self.assertEqual(ctrl.lightingMode, 2)
            ctrl.pressButton('btn_lamp')
            self.assertTrue(ctrl.deskLampOn)

            # Camera presets + Camera Fix toggle (F key)
            for preset in range(len(CAMERA_PRESETS)):
                ctrl.setCameraPreset(preset)
                self.assertEqual(ctrl.cameraPreset, preset)
            self.assertFalse(ctrl.cameraFixed)
            ctrl.toggleCameraFix()
            self.assertTrue(ctrl.cameraFixed)
            ctrl.toggleCameraFix()
            self.assertFalse(ctrl.cameraFixed)

            # Hybrid raytracing toggle (R key) & shader paths
            self.assertTrue(ctrl.raytracingEnabled)
            self.assertTrue(ctrl.shaderBaseUrl.endswith('/shaders/'))
            ctrl.toggleRaytracing()
            self.assertFalse(ctrl.raytracingEnabled)
            ctrl.toggleRaytracing()
            self.assertTrue(ctrl.raytracingEnabled)

            # Interactive 5-page tattered notepad pickup, page flipping 1->5, and set down
            self.assertFalse(ctrl.notepadOpen)
            ctrl.clickNotepad()
            self.assertTrue(ctrl.notepadOpen)
            self.assertEqual(ctrl.notepadPage, 0)
            for expected_page in range(1, 5):
                ctrl.clickNotepad()
                self.assertEqual(ctrl.notepadPage, expected_page)
            ctrl.clickNotepad()
            self.assertFalse(ctrl.notepadOpen)

            # Interactive bobblehead poke
            ctrl.pokeBobblehead()
            ctrl.step_physics(1.0 / 60.0)
            self.assertGreater(abs(ctrl.bobbleRx) + abs(ctrl.bobbleRz), 0.5)

            # Camera zoom with [ and ] keys
            ctrl.adjustZoom(36.0)
            self.assertAlmostEqual(ctrl.zoomOffset, 36.0, places=2)
            ctrl.adjustZoom(-72.0)
            self.assertAlmostEqual(ctrl.zoomOffset, -36.0, places=2)
            ctrl.setCameraPreset(0)
            self.assertAlmostEqual(ctrl.zoomOffset, 0.0, places=2)

            # Interactive Verlet coax cables: unplug both, drag-to-replug & swap CH1/CH2 inputs
            self.assertTrue(ctrl.ch1Plugged)
            self.assertTrue(ctrl.ch2Plugged)
            self.assertFalse(ctrl.inputsSwapped)
            ctrl.grabCable(0)
            ctrl.dragCable(0, 120.0, 80.0)
            ctrl.releaseCable(0)
            self.assertFalse(ctrl.ch1Plugged)
            ctrl.grabCable(1)
            ctrl.dragCable(1, 120.0, 80.0)
            ctrl.releaseCable(1)
            self.assertFalse(ctrl.ch2Plugged)
            # Drag cable 1 (Blue / Y) into CH1 port and cable 0 (Red / X) into CH2 port
            ctrl.replugPort(0)
            ctrl.cables[0].port_idx = 1
            ctrl.cables[1].port_idx = 0
            self.assertTrue(ctrl.inputsSwapped)
            routed = ctrl.route_signal(np.array([[0.25, -0.75]], dtype=np.float32))
            np.testing.assert_allclose(routed, [[-0.75, 0.25]], atol=1e-5)
            # Restore default routing
            ctrl.cables[0].replug(0)
            ctrl.cables[1].replug(1)
            self.assertFalse(ctrl.inputsSwapped)

            # Working yellow PWR button: collapse-to-dot shutdown and warm-up bloom
            self.assertTrue(ctrl.crtPowered)
            self.assertAlmostEqual(ctrl.crtPowerLevel, 1.0, places=3)
            ctrl.pressButton('btn_power')
            self.assertFalse(ctrl.crtPowered)
            for _ in range(18):
                ctrl.step_physics(0.02)
            self.assertGreater(ctrl.crtDotGlow, 0.2)
            for _ in range(30):
                ctrl.step_physics(0.02)
            self.assertAlmostEqual(ctrl.crtPowerLevel, 0.0, places=3)
            ctrl.pressButton('btn_power')
            self.assertTrue(ctrl.crtPowered)
            for _ in range(80):
                ctrl.step_physics(0.02)
            self.assertAlmostEqual(ctrl.crtPowerLevel, 1.0, places=3)

            # Physical 3D knobs: CH1 X Gain, CH2 Y Gain (+ double-click reset), and Time Window
            ctrl.setKnob('knob_gain_x', 0.5)
            self.assertGreater(ctrl.gainX, 3.0)
            self.assertTrue(ctrl.lcdGainX.endswith('×'))
            ctrl.resetKnob('knob_gain_x')
            self.assertAlmostEqual(ctrl.gainX, 1.0, places=4)
            self.assertEqual(ctrl.lcdGainX, '1.00×')

            ctrl.setKnob('knob_gain_y', 0.6)
            self.assertGreater(ctrl.gainY, 4.0)
            ctrl.resetKnob('knob_gain_y')
            self.assertAlmostEqual(ctrl.gainY, 1.0, places=4)

            self.assertFalse(win.sweep_mode)
            self.assertEqual(ctrl.lcdTimeWin, 'X–Y')
            before_win = win.sweep_window_ms
            ctrl.setKnob('knob_time_win', 0.8)
            self.assertAlmostEqual(win.sweep_window_ms, before_win, places=4)

            ctrl.pressButton('btn_view')
            self.assertTrue(win.sweep_mode)
            ctrl.setKnob('knob_time_win', 0.8)
            self.assertNotAlmostEqual(win.sweep_window_ms, before_win, places=2)
            self.assertTrue(ctrl.lcdTimeWin.endswith(('ms', 's')))

            ctrl.setKnob('knob_output', 0.5)
            self.assertAlmostEqual(win.scene.settings.drawing_gain, 0.475, places=3)

            # Physical 3D pushbuttons (Grid + Object + 3D-only Beam/PAL)
            before_grid = win.preferences['show_grid']
            ctrl.pressButton('btn_grid')
            self.assertEqual(win.preferences['show_grid'], not before_grid)
            win.add_object('Cube')
            before_solid = win.scene.shape.solid
            ctrl.pressButton('btn_object')
            self.assertNotEqual(win.scene.shape.solid, before_solid)

            orig_pal = win.preferences['palette']
            orig_phos = win.preferences['phosphor']
            orig_beam_idx = ctrl.beamPresetIndex
            orig_bg_idx = ctrl.crtBackingIndex
            ctrl.pressButton('btn_beam')
            self.assertNotEqual(ctrl.beamPresetIndex, orig_beam_idx)
            ctrl.pressButton('btn_palette')
            self.assertNotEqual(ctrl.crtBackingIndex, orig_bg_idx)
            self.assertEqual(win.preferences['palette'], orig_pal)
            self.assertEqual(win.preferences['phosphor'], orig_phos)

            self.assertTrue(ctrl.sampleRateLabel.endswith('kHz'))

            # Clean exit back to 2D editor
            ctrl.exit3D()
            app.processEvents()
            self.assertFalse(win.desk3d)
            self.assertIs(win.host, win.main_host)
        finally:
            win.force_close = True
            win.close()


if __name__ == '__main__':
    unittest.main()
