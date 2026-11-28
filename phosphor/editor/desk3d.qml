import QtQuick
import QtQuick3D
import QtQuick3D.Helpers

Item {
    id: root
    anchors.fill: parent
    focus: true

    property real parallaxX: 0.0
    property real parallaxY: 0.0
    property real effParallaxX: deskCtrl.cameraFixed ? 0.0 : parallaxX
    property real effParallaxY: deskCtrl.cameraFixed ? 0.0 : parallaxY

    Behavior on parallaxX { NumberAnimation { duration: 180; easing.type: Easing.OutQuad } }
    Behavior on parallaxY { NumberAnimation { duration: 180; easing.type: Easing.OutQuad } }
    Behavior on effParallaxX { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
    Behavior on effParallaxY { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }

    // Camera preset targets: 0 = Desk (3/4 heroic C1-170 angle), 1 = Scope Close-up, 2 = Controls Close-up
    property vector3d camBasePos: deskCtrl.cameraPreset === 1
        ? Qt.vector3d(-112, 6, 206)
        : (deskCtrl.cameraPreset === 2 ? Qt.vector3d(96, -2, 218) : Qt.vector3d(-6, 56, 622))
    property vector3d camBaseRot: deskCtrl.cameraPreset === 1
        ? Qt.vector3d(-2.5, 1.5, 0)
        : (deskCtrl.cameraPreset === 2 ? Qt.vector3d(-3.0, -2.5, 0) : Qt.vector3d(-7.2, 1.0, 0))
    property real camFov: deskCtrl.cameraPreset === 0 ? 36 : 34

    property real camX: camBasePos.x
    property real camY: camBasePos.y
    property real camZ: camBasePos.z + deskCtrl.zoomOffset
    property real camRx: camBaseRot.x
    property real camRy: camBaseRot.y

    Behavior on camX { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }
    Behavior on camY { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }
    Behavior on camZ { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
    Behavior on camRx { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }
    Behavior on camRy { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }
    Behavior on camFov { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }

    // Keyboard shortcuts: 1/2/3 presets, [ / ] zoom out/in, F camera fix, L lighting, Space play, Esc close notepad / exit
    Keys.onPressed: function(event) {
        if (event.key === Qt.Key_1) { deskCtrl.setCameraPreset(0); event.accepted = true }
        else if (event.key === Qt.Key_2) { deskCtrl.setCameraPreset(1); event.accepted = true }
        else if (event.key === Qt.Key_3) { deskCtrl.setCameraPreset(2); event.accepted = true }
        else if (event.key === Qt.Key_BracketLeft) { deskCtrl.adjustZoom(36.0); event.accepted = true }
        else if (event.key === Qt.Key_BracketRight) { deskCtrl.adjustZoom(-36.0); event.accepted = true }
        else if (event.key === Qt.Key_F) { deskCtrl.toggleCameraFix(); event.accepted = true }
        else if (event.key === Qt.Key_R) { deskCtrl.toggleRaytracing(); event.accepted = true }
        else if (event.key === Qt.Key_T) { deskCtrl.togglePathTrace(); event.accepted = true }
        else if (event.key === Qt.Key_L) { deskCtrl.cycleLighting(); event.accepted = true }
        else if (event.key === Qt.Key_P || event.key === Qt.Key_F12) { deskCtrl.captureSnapshot(); event.accepted = true }
        else if (event.key === Qt.Key_Space) { deskCtrl.pressButton("btn_play"); event.accepted = true }
        else if (event.key === Qt.Key_Escape) {
            if (deskCtrl.notepadOpen) deskCtrl.closeNotepad()
            else deskCtrl.exit3D()
            event.accepted = true
        }
    }

    View3D {
        id: view3d
        anchors.fill: parent

        property real bezelBounceGain: deskCtrl.lightingMode === 2 ? 0.85
                                     : (deskCtrl.lightingMode === 1 ? 0.50 : 0.16)

        environment: ExtendedSceneEnvironment {
            backgroundMode: SceneEnvironment.Color
            clearColor: deskCtrl.fluorescentOn ? "#cbd3db"
                      : (deskCtrl.deskLampOn ? "#070b12" : "#010203")
            lightProbe: Texture {
                source: deskCtrl.textureBaseUrl + "lab_env.hdr"
                mappingMode: Texture.Environment
            }
            probeExposure: deskCtrl.fluorescentOn ? 0.36
                         : (deskCtrl.deskLampOn ? 0.045 : 0.0)
            Behavior on probeExposure { NumberAnimation { duration: 380 } }
            antialiasingMode: SceneEnvironment.MSAA
            antialiasingQuality: SceneEnvironment.High
            specularAAEnabled: true
            tonemapMode: SceneEnvironment.TonemapModeFilmic
            ditheringEnabled: true

            // Screen-Space Ambient Occlusion (SSAO) tuned for crisp mechanical crevice grounding
            aoEnabled: deskCtrl.raytracingEnabled
            aoStrength: 68
            aoDistance: 11
            aoSoftness: 24
            aoBias: 0.04
            aoSampleRate: deskCtrl.textureTier === "2k" ? 3 : 2
            aoDither: true

            // Subtle optical HDR bloom restricted strictly to emissive phosphor, LEDs, and warm bulb
            glowEnabled: true
            glowQualityHigh: deskCtrl.textureTier === "2k"
            glowUseBicubicUpscale: deskCtrl.textureTier === "2k"
            glowStrength: 0.30
            glowIntensity: 0.28
            glowBloom: 0.30
            glowHDRMinimumValue: 1.35
            glowBlendMode: ExtendedSceneEnvironment.Additive

            // Subtle optical camera lens vignette (vignetteStrength=15 keeps center 100% bright; small radius = gentle corners)
            vignetteEnabled: true
            vignetteStrength: 15.0
            vignetteRadius: deskCtrl.fluorescentOn ? 0.10 : 0.18
            vignetteColor: "#040507"

            // Macro depth-of-field when zoomed into Scope or Controls presets
            depthOfFieldEnabled: deskCtrl.cameraPreset !== 0 && !deskCtrl.notepadOpen
            depthOfFieldFocusDistance: deskCtrl.cameraPreset === 1 ? 210 : 220
            depthOfFieldFocusRange: 115
            depthOfFieldBlurAmount: 2.2

            // Screen-Space Reflections (SSR) post-processing pass on the bench & cutting mat
            effects: [ ssrEffect ]
        }

        Effect {
            id: ssrEffect
            property real ssrStrength: deskCtrl.raytracingEnabled
                ? (deskCtrl.cameraPreset === 0 ? 1.0 : 0.35) : 0.0
            property real camFov: root.camFov
            property real highQuality: deskCtrl.textureTier === "2k" ? 1.0 : 0.0
            passes: Pass {
                shaders: Shader {
                    stage: Shader.Fragment
                    shader: deskCtrl.shaderBaseUrl + "bench_ssr.frag"
                }
            }
        }

        // Event-driven parallax-corrected ReflectionProbe cubemap (refreshes cleanly on lighting/power changes,
        // avoiding per-frame cubemap stalls and frameSwapped pacing conflicts with PhosphorLayer's decay loop)
        ReflectionProbe {
            id: benchReflectionProbe
            position: Qt.vector3d(0, 8, 95)
            boxSize: Qt.vector3d(900, 620, 720)
            parallaxCorrection: true
            clearColor: deskCtrl.fluorescentOn ? "#cbd3db"
                      : (deskCtrl.deskLampOn ? "#0c0f15" : "#010203")
            quality: deskCtrl.textureTier === "2k" ? ReflectionProbe.Medium : ReflectionProbe.Low
            refreshMode: ReflectionProbe.FirstFrame
            timeSlicing: ReflectionProbe.AllFacesAtOnce
        }
        Timer {
            id: probeRefreshTimer
            interval: 180
            repeat: false
            onTriggered: benchReflectionProbe.scheduleUpdate()
        }
        Connections {
            target: deskCtrl
            property int lastLightMode: -1
            property bool lastPowered: true
            function onStateChanged() {
                if (!deskCtrl) return
                if (deskCtrl.lightingMode !== lastLightMode || deskCtrl.crtPowered !== lastPowered) {
                    lastLightMode = deskCtrl.lightingMode
                    lastPowered = deskCtrl.crtPowered
                    probeRefreshTimer.restart()
                }
            }
        }

        // --- Quality-coupled 1K / 2K seamless CC0 PBR textures ---
        Texture {
            id: chassisAlbedoTex
            source: deskCtrl.textureBaseUrl + "chassis_albedo.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 1.5; scaleV: 1.5
        }
        Texture {
            id: chassisNormalTex
            source: deskCtrl.textureBaseUrl + "chassis_normal.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 1.5; scaleV: 1.5
        }
        Texture {
            id: chassisRoughnessTex
            source: deskCtrl.textureBaseUrl + "chassis_roughness.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 1.5; scaleV: 1.5
        }
        Texture {
            id: aluminumAlbedoTex
            source: deskCtrl.textureBaseUrl + "aluminum_albedo.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 1.0; scaleV: 1.0
        }
        Texture {
            id: aluminumNormalTex
            source: deskCtrl.textureBaseUrl + "aluminum_normal.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 1.0; scaleV: 1.0
        }
        Texture {
            id: aluminumRoughnessTex
            source: deskCtrl.textureBaseUrl + "aluminum_roughness.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 1.0; scaleV: 1.0
        }
        Texture {
            id: deskAlbedoTex
            source: deskCtrl.textureBaseUrl + "desk_albedo.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 2.2; scaleV: 1.5
        }
        Texture {
            id: deskNormalTex
            source: deskCtrl.textureBaseUrl + "desk_normal.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 2.2; scaleV: 1.5
        }
        Texture {
            id: deskRoughnessTex
            source: deskCtrl.textureBaseUrl + "desk_roughness.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 2.2; scaleV: 1.5
        }
        Texture {
            id: wallAlbedoTex
            source: deskCtrl.textureBaseUrl + "wall_albedo.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 3.0; scaleV: 1.8
        }
        Texture {
            id: wallNormalTex
            source: deskCtrl.textureBaseUrl + "wall_normal.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 3.0; scaleV: 1.8
        }
        Texture {
            id: wallRoughnessTex
            source: deskCtrl.textureBaseUrl + "wall_roughness.jpg"
            tilingModeHorizontal: Texture.Repeat; tilingModeVertical: Texture.Repeat
            scaleU: 3.0; scaleV: 1.8
        }
        Texture { id: faceplateAlbedoTex; textureData: faceplateAlbedoData }
        Texture { id: faceplateEmissiveTex; textureData: faceplateEmissiveData }
        Texture { id: sideVentTex; textureData: sideVentData }
        Texture {
            id: crtGlassOverlayTex
            textureData: crtGlassOverlayData
            tilingModeHorizontal: Texture.ClampToEdge
            tilingModeVertical: Texture.ClampToEdge
        }
        Texture { id: bezelSmudgeTex; textureData: bezelSmudgeData }
        Texture { id: bezelGougeTex; textureData: bezelGougeData }
        Texture { id: cuttingMatTex; textureData: cuttingMatData }
        Texture { id: cuttingMatNormalTex; textureData: cuttingMatNormalData }
        Texture { id: cuttingMatRoughnessTex; textureData: cuttingMatRoughnessData }
        Texture { id: calStickerTex; textureData: calStickerData }
        Texture { id: bobbleFaceTex; textureData: bobbleFaceData }
        Texture { id: notepadPageTex; textureData: notepadPageData }
        Texture { id: contactShadowTex; textureData: contactShadowData }
        Texture { id: labWallChartTex; textureData: labWallChartData }
        Texture { id: crtLiveTex; sourceItem: crtSource }

        // --- Shared PBR Materials ---
        PrincipledMaterial {
            id: chassisPlasticMat
            baseColorMap: chassisAlbedoTex
            normalMap: chassisNormalTex
            normalStrength: 0.28
            roughnessMap: chassisRoughnessTex
            roughnessChannel: Material.R
            roughness: 0.90
            metalness: 0.0
            specularAmount: 0.22
        }
        PrincipledMaterial {
            id: handlePlasticMat
            baseColorMap: chassisAlbedoTex
            normalMap: chassisNormalTex
            normalStrength: 0.24
            roughnessMap: chassisRoughnessTex
            roughnessChannel: Material.R
            baseColor: "#d2cab8"
            roughness: 0.86
            metalness: 0.0
            specularAmount: 0.25
        }
        PrincipledMaterial {
            id: lightKnobMat
            baseColor: "#cfc9bc"
            roughness: 0.54
            metalness: 0.0
            specularAmount: 0.34
            clearcoatAmount: 0.26
            clearcoatRoughnessAmount: 0.36
        }
        PrincipledMaterial {
            id: brushedSteelMat
            baseColorMap: aluminumAlbedoTex
            normalMap: aluminumNormalTex
            normalStrength: 0.14
            baseColor: "#dedfd9"
            roughness: 0.24
            metalness: 0.88
        }
        PrincipledMaterial {
            id: vintageLampMetalMat
            baseColorMap: aluminumAlbedoTex
            baseColor: "#353d39"
            roughness: 0.30
            metalness: 0.72
            clearcoatAmount: 0.55
            clearcoatRoughnessAmount: 0.22
        }
        PrincipledMaterial {
            id: contactShadowMat
            lighting: PrincipledMaterial.NoLighting
            baseColorMap: contactShadowTex
            alphaMode: PrincipledMaterial.Blend
            depthDrawMode: Material.NeverDepthDraw
            opacity: deskCtrl.fluorescentOn ? 0.62 : (deskCtrl.deskLampOn ? 0.78 : 0.45)
        }
        PrincipledMaterial {
            id: brassAccentMat
            baseColor: "#c5a059"
            roughness: 0.30
            metalness: 0.88
        }
        PrincipledMaterial {
            id: darkRubberMat
            baseColor: "#222426"
            roughness: 0.88
            metalness: 0.0
            specularAmount: 0.18
        }
        PrincipledMaterial {
            id: greyPillMat
            baseColor: "#d9d4c7"
            roughness: 0.50
            metalness: 0.0
            specularAmount: 0.32
            clearcoatAmount: 0.34
            clearcoatRoughnessAmount: 0.28
        }
        PrincipledMaterial {
            id: greenPillMat
            baseColor: "#1e9e54"
            roughness: 0.40
            metalness: 0.0
            specularAmount: 0.36
            clearcoatAmount: 0.45
            clearcoatRoughnessAmount: 0.22
        }
        PrincipledMaterial {
            id: yellowPillMat
            baseColor: "#e8b923"
            roughness: 0.38
            metalness: 0.0
            specularAmount: 0.36
            clearcoatAmount: 0.45
            clearcoatRoughnessAmount: 0.22
        }
        PrincipledMaterial {
            id: darkPointerMat
            baseColor: "#1d1f22"
            roughness: 0.48
            metalness: 0.0
            emissiveFactor: deskCtrl.lightingMode === 2
                ? Qt.vector3d(0.28, 0.32, 0.25) : Qt.vector3d(0, 0, 0)
        }

        // --- Camera with subtle damped mouse parallax (disabled when Camera Fix is active) ---
        PerspectiveCamera {
            id: camera
            position: Qt.vector3d(
                root.camX + root.effParallaxX * (deskCtrl.cameraPreset === 0 ? 16.0 : 6.0),
                root.camY - root.effParallaxY * (deskCtrl.cameraPreset === 0 ? 10.0 : 4.0),
                root.camZ
            )
            eulerRotation: Qt.vector3d(
                root.camRx - root.effParallaxY * 2.0,
                root.camRy - root.effParallaxX * 2.8,
                0
            )
            fieldOfView: root.camFov
            clipNear: 10
            clipFar: 2500
        }

        // === PHYSICAL LAB LIGHTING (TOTAL ACTIVE LIGHTS <= 13, SAFELY WITHIN QTQUICK3D 15-LIGHT LIMIT) ===
        // 1 & 2: Overhead fluorescent shop lights with Cascaded Shadow Maps (quality-coupled 3-split on 2K)
        DirectionalLight {
            id: overheadLight
            visible: deskCtrl.fluorescentOn
            eulerRotation: Qt.vector3d(-46, 24, 0)
            color: "#f4f8ff"
            brightness: 0.76
            ambientColor: "#383e46"
            castsShadow: true
            shadowMapQuality: deskCtrl.textureTier === "2k" ? Light.ShadowMapQualityVeryHigh : Light.ShadowMapQualityHigh
            softShadowQuality: deskCtrl.raytracingEnabled ? Light.PCF16 : Light.Hard
            csmNumSplits: deskCtrl.textureTier === "2k" ? 3 : 1
            csmSplit1: 0.12
            csmSplit2: 0.34
            csmSplit3: 0.70
            csmBlendRatio: 0.08
            shadowMapFar: 1400
            pcfFactor: 2.2
            shadowBias: 1.20
            shadowFactor: 82
        }
        DirectionalLight {
            id: fillLight
            visible: deskCtrl.fluorescentOn
            eulerRotation: Qt.vector3d(-20, -45, 0)
            color: "#dce9f8"
            brightness: 0.22
        }
        // Subtle warm oak upward desk bounce in fluorescent mode
        PointLight {
            id: warmDeskBounce
            visible: deskCtrl.fluorescentOn
            position: Qt.vector3d(0, -96, 150)
            color: "#e2ae74"
            brightness: 0.16
            constantFade: 0.65; linearFade: 0.25; quadraticFade: 0.20
            castsShadow: false
        }
        // Subtle cool night-room rim/fill in Lamp-only mode for dramatic warm-pool + cool dark falloff
        DirectionalLight {
            id: coolNightFill
            visible: deskCtrl.lightingMode === 1
            eulerRotation: Qt.vector3d(-24, -56, 0)
            color: "#5280c4"
            brightness: 0.24
            ambientColor: "#0c1524"
            castsShadow: false
        }
        // 3: Architect desk lamp SpotLight is parented directly inside architectLamp's spun-metal shade below

        // 4: Primary signal-reactive CRT phosphor light (shadowless so centroid motion never triggers 6-face shadow stalls)
        PointLight {
            id: crtPhosphorLight
            position: Qt.vector3d(
                -112 + deskCtrl.crtCentroidX * 48,
                6 + deskCtrl.crtCentroidY * 44,
                32
            )
            color: deskCtrl.crtColor
            brightness: (deskCtrl.lightingMode === 2 ? 1.65 : (deskCtrl.lightingMode === 1 ? 0.92 : 0.24))
                        * deskCtrl.crtEnergy * deskCtrl.crtPowerLevel
            constantFade: 0.32
            linearFade: 0.95
            quadraticFade: 5.2
            castsShadow: false
        }
        // 5: Bottom-right amber MAINS / PWR LED local glow
        PointLight {
            id: pwrLedLight
            visible: deskCtrl.crtPowered
            position: Qt.vector3d(192, -84, 18)
            color: "#ffbf38"
            brightness: deskCtrl.lightingMode === 2 ? 0.52 : (deskCtrl.lightingMode === 1 ? 0.22 : 0.0)
            constantFade: 0.35
            linearFade: 1.8
            quadraticFade: 8.5
        }

        // === LAB BENCH (CC0 OAK VENEER), UNDER-BENCH APRON, BACK WALL (CC0 PLASTER), & CUTTING MAT ===
        Model {
            source: "#Cube"
            position: Qt.vector3d(60, -138, -130)
            scale: Qt.vector3d(12.5, 0.16, 7.2)
            receivesShadows: true
            materials: PrincipledMaterial {
                baseColorMap: deskAlbedoTex
                normalMap: deskNormalTex
                normalStrength: 0.52
                roughnessMap: deskRoughnessTex
                roughnessChannel: Material.R
                roughness: 0.78
                metalness: 0.02
                clearcoatAmount: 0.24
                clearcoatRoughnessAmount: 0.32
            }
        }
        // Rounded oak front bullnose edge molding along the workbench front lip (z = +230)
        Model {
            source: "#Cylinder"
            position: Qt.vector3d(60, -138, 230)
            eulerRotation.z: 90
            scale: Qt.vector3d(0.16, 12.5, 0.16)
            receivesShadows: true
            materials: PrincipledMaterial {
                baseColorMap: deskAlbedoTex
                normalMap: deskNormalTex
                normalStrength: 0.42
                roughness: 0.72
                metalness: 0.02
                clearcoatAmount: 0.24
                clearcoatRoughnessAmount: 0.32
            }
        }
        // Recessed dark-steel workbench under-bench apron & shadow void blocker (prevents sky gap when zoomed out)
        Model {
            source: "#Cube"
            position: Qt.vector3d(60, -256, 198)
            scale: Qt.vector3d(13.0, 2.2, 0.24)
            receivesShadows: true
            materials: PrincipledMaterial {
                baseColor: "#1a1c1e"
                roughness: 0.85
                metalness: 0.35
            }
        }
        Model {
            source: "#Cube"
            position: Qt.vector3d(60, -370, 80)
            scale: Qt.vector3d(24.0, 0.2, 16.0)
            materials: PrincipledMaterial {
                baseColor: "#141517"
                roughness: 0.96
                metalness: 0.0
            }
        }
        // Green self-healing lab cutting mat seated on the oak bench beneath the scope (albedo + knife-cut normal + wear roughness)
        Model {
            source: "#Cube"
            position: Qt.vector3d(18, -129.4, -22)
            scale: Qt.vector3d(6.8, 0.012, 3.6)
            receivesShadows: true
            materials: PrincipledMaterial {
                baseColorMap: cuttingMatTex
                normalMap: cuttingMatNormalTex
                normalStrength: 0.45
                roughnessMap: cuttingMatRoughnessTex
                roughnessChannel: Material.R
                roughness: 0.90
                metalness: 0.0
                specularAmount: 0.22
                clearcoatAmount: 0.08
                clearcoatRoughnessAmount: 0.52
            }
        }
        Model {
            source: "#Cube"
            position: Qt.vector3d(40, 20, -360)
            scale: Qt.vector3d(18.0, 10.0, 0.2)
            receivesShadows: true
            materials: PrincipledMaterial {
                baseColorMap: wallAlbedoTex
                normalMap: wallNormalTex
                normalStrength: 0.35
                roughnessMap: wallRoughnessTex
                roughnessChannel: Material.R
                baseColor: "#b5b2a8"
                roughness: 0.94
                metalness: 0.0
            }
        }
        // === BACK WALL LATE-80s LAB STORYTELLING: BACKSPLASH, EMT CONDUIT, POWER STRIP & LISSAJOUS WALL CHART ===
        // Dark workbench backsplash trim sealing the oak bench to the plaster wall
        Model {
            source: "#Cube"
            position: Qt.vector3d(40, -118, -349)
            scale: Qt.vector3d(16.0, 0.28, 0.08)
            receivesShadows: true
            materials: PrincipledMaterial { baseColor: "#2e2822"; roughness: 0.82; metalness: 0.0 }
        }
        // Horizontal galvanized steel EMT electrical conduit pipe & clamps along the back wall
        Model {
            source: "#Cylinder"
            position: Qt.vector3d(40, -48, -346)
            eulerRotation.z: 90
            scale: Qt.vector3d(0.065, 16.0, 0.065)
            castsShadows: true; receivesShadows: true
            materials: brushedSteelMat
        }
        Repeater3D {
            model: [-320, -140, 160, 340]
            Model {
                source: "#Cube"
                position: Qt.vector3d(modelData, -48, -347)
                scale: Qt.vector3d(0.06, 0.14, 0.09)
                materials: brushedSteelMat
            }
        }
        // Industrial steel benchtop power strip tucked on the rear-right workbench
        Node {
            position: Qt.vector3d(296, -124, -172)
            eulerRotation.y: -10
            Model {
                source: "#Cube"
                scale: Qt.vector3d(0.42, 0.12, 1.55)
                castsShadows: true; receivesShadows: true
                materials: PrincipledMaterial { baseColor: "#505458"; roughness: 0.48; metalness: 0.65 }
            }
            // 4 Recessed dark duplex outlet faces
            Repeater3D {
                model: [-50, -18, 14, 46]
                Model {
                    source: "#Cube"
                    position: Qt.vector3d(0, 6.2, modelData)
                    scale: Qt.vector3d(0.24, 0.015, 0.22)
                    materials: darkRubberMat
                }
            }
            // Tiny glowing orange-red neon mains rocker switch (emissive only, zero extra light cost)
            Model {
                source: "#Cube"
                position: Qt.vector3d(0, 6.8, 66)
                eulerRotation.x: 8
                scale: Qt.vector3d(0.14, 0.03, 0.10)
                materials: PrincipledMaterial {
                    baseColor: "#e84a1f"
                    emissiveFactor: Qt.vector3d(1.65, 0.34, 0.08)
                    roughness: 0.25
                }
            }
        }
        // Vintage Lissajous & harmonic ratio reference chart pinned to the upper-right plaster wall
        Node {
            position: Qt.vector3d(344, 150, -349)
            eulerRotation.z: -1.4
            Model {
                source: "#Rectangle"
                scale: Qt.vector3d(1.62, 1.22, 1.0)
                receivesShadows: true
                materials: PrincipledMaterial {
                    baseColorMap: labWallChartTex
                    roughness: 0.90
                    metalness: 0.0
                }
            }
            // Two tiny brass thumbtacks pinning the top corners
            Repeater3D {
                model: [-68, 68]
                Model {
                    source: "#Cylinder"
                    position: Qt.vector3d(modelData, 53, 1.0)
                    eulerRotation.x: 90
                    scale: Qt.vector3d(0.042, 0.018, 0.042)
                    materials: brassAccentMat
                }
            }
        }

        // === CONTACT-SHADOW GROUNDING DECALS (ANCHORS HEAVY PROPS TO BENCH & CUTTING MAT) ===
        // Soft shadow under the cutting mat perimeter on the oak bench
        Model {
            source: "#Rectangle"
            position: Qt.vector3d(18, -129.85, -22)
            eulerRotation.x: -90
            scale: Qt.vector3d(7.15, 3.85, 1.0)
            materials: contactShadowMat
        }
        // Soft shadow directly under the C1-170 bail handle & chassis underside
        Model {
            source: "#Rectangle"
            position: Qt.vector3d(0, -128.55, -20)
            eulerRotation.x: -90
            scale: Qt.vector3d(5.40, 1.55, 1.0)
            materials: contactShadowMat
        }
        Model {
            source: "#Rectangle"
            position: Qt.vector3d(0, -128.55, -145)
            eulerRotation.x: -90
            scale: Qt.vector3d(5.10, 2.35, 1.0)
            materials: contactShadowMat
        }
        // Soft shadow under the heavy cast-metal architect lamp base
        Model {
            source: "#Rectangle"
            position: Qt.vector3d(-286, -128.55, 26)
            eulerRotation.x: -90
            scale: Qt.vector3d(1.12, 1.12, 1.0)
            materials: contactShadowMat
        }
        // Soft shadow under the red industrial fluorescent pushbutton box
        Model {
            source: "#Rectangle"
            position: Qt.vector3d(-215, -128.55, 96)
            eulerRotation: Qt.vector3d(-90, 18, 0)
            scale: Qt.vector3d(0.76, 0.76, 1.0)
            materials: contactShadowMat
        }
        // Soft shadow under the resting lab notepad (hidden while picked up)
        Model {
            visible: !deskCtrl.notepadOpen
            source: "#Rectangle"
            position: Qt.vector3d(182, -128.55, 118)
            eulerRotation: Qt.vector3d(-90, -18, 0)
            scale: Qt.vector3d(0.95, 1.18, 1.0)
            materials: contactShadowMat
        }
        // Soft shadow under the ceramic lab coffee mug (right bench)
        Model {
            source: "#Rectangle"
            position: Qt.vector3d(268, -129.65, 86)
            eulerRotation.x: -90
            scale: Qt.vector3d(0.66, 0.66, 1.0)
            materials: contactShadowMat
        }
        // Soft shadow under the spare coiled BNC probe (right-rear bench)
        Model {
            source: "#Rectangle"
            position: Qt.vector3d(280, -128.55, -42)
            eulerRotation.x: -90
            scale: Qt.vector3d(0.88, 0.88, 1.0)
            materials: contactShadowMat
        }

        // === BRAIDED ARCHITECT LAMP POWER CORD TRAILING TO BACK WALL ===
        Model {
            geometry: lampCordMesh
            castsShadows: true; receivesShadows: true
            materials: darkRubberMat
        }

        // === VINTAGE GLAZED CERAMIC LAB COFFEE MUG (TIES TO NOTEPAD COFFEE RINGS) ===
        Node {
            id: labCoffeeMug
            position: Qt.vector3d(268, -130, 86)
            eulerRotation.y: -48
            // Glazed warm stoneware body
            Model {
                geometry: coffeeMugMesh
                castsShadows: true; receivesShadows: true
                materials: PrincipledMaterial {
                    baseColor: "#ece2cf"
                    roughness: 0.34
                    metalness: 0.0
                    clearcoatAmount: 0.68
                    clearcoatRoughnessAmount: 0.16
                }
            }
            // Ergonomic glazed C-handle
            Model {
                geometry: mugHandleMesh
                castsShadows: true; receivesShadows: true
                materials: PrincipledMaterial {
                    baseColor: "#ece2cf"
                    roughness: 0.34
                    metalness: 0.0
                    clearcoatAmount: 0.68
                    clearcoatRoughnessAmount: 0.16
                }
            }
            // Vintage cobalt-teal accent stripe around upper mug rim
            Model {
                source: "#Cylinder"
                position: Qt.vector3d(0, 39.5, 0)
                scale: Qt.vector3d(0.412, 0.032, 0.412)
                materials: PrincipledMaterial {
                    baseColor: "#24576b"
                    roughness: 0.28
                    clearcoatAmount: 0.72
                    clearcoatRoughnessAmount: 0.16
                }
            }
            // Glossy dark-roast black coffee surface inside the mug
            Model {
                source: "#Cylinder"
                position: Qt.vector3d(0, 36.2, 0)
                scale: Qt.vector3d(0.345, 0.01, 0.345)
                materials: PrincipledMaterial {
                    baseColor: "#140b06"
                    roughness: 0.06
                    metalness: 0.0
                    clearcoatAmount: 1.0
                    clearcoatRoughnessAmount: 0.04
                }
            }
        }

        // === CLASSIC HEXAGONAL YELLOW NO. 2 LAB PENCIL NEXT TO THE NOTEPAD ===
        Node {
            id: labPencil
            position: Qt.vector3d(104, -126.8, 128)
            eulerRotation.y: 26
            // Yellow lacquered hexagonal wood shaft
            Model {
                geometry: pencilBarrelMesh
                castsShadows: true; receivesShadows: true
                materials: PrincipledMaterial {
                    baseColor: "#e6b422"
                    roughness: 0.34
                    clearcoatAmount: 0.45
                    clearcoatRoughnessAmount: 0.26
                }
            }
            // Crimped brass eraser ferrule
            Model {
                source: "#Cylinder"
                position: Qt.vector3d(-39.5, 0, 0)
                eulerRotation.z: 90
                scale: Qt.vector3d(0.044, 0.065, 0.044)
                castsShadows: true
                materials: brassAccentMat
            }
            // Pink rubber eraser tip
            Model {
                source: "#Cylinder"
                position: Qt.vector3d(-44.0, 0, 0)
                eulerRotation.z: 90
                scale: Qt.vector3d(0.041, 0.055, 0.041)
                castsShadows: true
                materials: PrincipledMaterial { baseColor: "#d97a7e"; roughness: 0.82 }
            }
            // Sharpened raw cedar wood cone
            Model {
                source: "#Cone"
                position: Qt.vector3d(41.5, 0, 0)
                eulerRotation.z: -90
                scale: Qt.vector3d(0.042, 0.095, 0.042)
                castsShadows: true
                materials: PrincipledMaterial { baseColor: "#cfa374"; roughness: 0.86 }
            }
            // Graphite pencil lead tip
            Model {
                source: "#Cone"
                position: Qt.vector3d(45.6, 0, 0)
                eulerRotation.z: -90
                scale: Qt.vector3d(0.014, 0.032, 0.014)
                materials: PrincipledMaterial { baseColor: "#26282a"; roughness: 0.40; metalness: 0.25 }
            }
        }

        // === SPARE COILED OSCILLOSCOPE PROBE RESTING ON RIGHT-REAR BENCH ===
        Node {
            id: spareProbe
            position: Qt.vector3d(280, -129.2, -44)
            eulerRotation.y: 18
            Model {
                geometry: spareProbeCordMesh
                castsShadows: true; receivesShadows: true
                materials: darkRubberMat
            }
            // Molded oscilloscope test probe wand + yellow collar + steel tip
            Node {
                position: Qt.vector3d(-12, 3.6, 46)
                eulerRotation.y: -62
                Model {
                    source: "#Cylinder"
                    eulerRotation.z: 90
                    scale: Qt.vector3d(0.065, 0.36, 0.065)
                    castsShadows: true
                    materials: PrincipledMaterial { baseColor: "#222428"; roughness: 0.50; clearcoatAmount: 0.25 }
                }
                Model {
                    source: "#Cylinder"
                    position.x: 19
                    eulerRotation.z: 90
                    scale: Qt.vector3d(0.072, 0.06, 0.072)
                    materials: yellowPillMat
                }
                Model {
                    source: "#Cone"
                    position.x: 27
                    eulerRotation.z: -90
                    scale: Qt.vector3d(0.022, 0.14, 0.022)
                    materials: brushedSteelMat
                }
            }
        }

        // === VINTAGE MULTI-HINGE METAL ARCHITECT DESK LAMP (LEFT OF BENCH) ===
        Node {
            id: architectLamp
            position: Qt.vector3d(-286, -130, 26)

            // Heavy cast-metal circular base
            Model {
                source: "#Cylinder"
                position: Qt.vector3d(0, 7, 0)
                scale: Qt.vector3d(0.76, 0.14, 0.76)
                castsShadows: true; receivesShadows: true
                materials: vintageLampMetalMat
            }
            Model {
                source: "#Cylinder"
                position: Qt.vector3d(0, 15, 0)
                scale: Qt.vector3d(0.42, 0.07, 0.42)
                castsShadows: true; receivesShadows: true
                materials: vintageLampMetalMat
            }
            // Clickable vintage on/off switch on the front of the lamp base
            Node {
                position: Qt.vector3d(20, 14, 24)
                Model {
                    source: "#Cube"
                    scale: Qt.vector3d(0.16, 0.05, 0.18)
                    materials: darkRubberMat
                }
                Model {
                    objectName: "btn_lamp"
                    pickable: true
                    source: "#Cylinder"
                    y: deskCtrl.deskLampOn ? 3.5 : 6.2
                    Behavior on y { NumberAnimation { duration: 90 } }
                    scale: Qt.vector3d(0.10, 0.08, 0.10)
                    castsShadows: true
                    materials: PrincipledMaterial {
                        baseColor: deskCtrl.deskLampOn ? "#e09b38" : "#6a6258"
                        emissiveFactor: deskCtrl.deskLampOn
                            ? Qt.vector3d(0.65, 0.36, 0.06) : Qt.vector3d(0.06, 0.04, 0.02)
                        roughness: 0.35
                    }
                }
            }
            // Lower twin-bar hinged arm angling up and slightly left
            Node {
                position: Qt.vector3d(0, 18, 0)
                eulerRotation: Qt.vector3d(-4, 18, 16)
                Model {
                    source: "#Cylinder"
                    position: Qt.vector3d(0, 96, -6)
                    scale: Qt.vector3d(0.07, 1.94, 0.07)
                    castsShadows: true
                    materials: vintageLampMetalMat
                }
                Model {
                    source: "#Cylinder"
                    position: Qt.vector3d(0, 96, 6)
                    scale: Qt.vector3d(0.07, 1.94, 0.07)
                    castsShadows: true
                    materials: vintageLampMetalMat
                }
                // True 3D helical coiled brass tension spring along lower arm
                Model {
                    geometry: lampSpringMesh
                    position: Qt.vector3d(-6.5, 86, 0)
                    castsShadows: true
                    materials: brassAccentMat
                }
                // Middle elbow hinge joint with knurled brass locking wing-nut
                Node {
                    position: Qt.vector3d(0, 192, 0)
                    Model {
                        source: "#Cylinder"
                        eulerRotation.x: 90
                        scale: Qt.vector3d(0.16, 0.22, 0.16)
                        materials: brassAccentMat
                    }
                    Model {
                        source: "#Cube"
                        position: Qt.vector3d(0, 0, 13)
                        eulerRotation.z: 28
                        scale: Qt.vector3d(0.18, 0.045, 0.03)
                        materials: brassAccentMat
                    }
                    // Upper arm reaching forward and right OVER the top-left of the scope
                    Node {
                        eulerRotation: Qt.vector3d(42, -12, -62)
                        Model {
                            source: "#Cylinder"
                            position: Qt.vector3d(0, 68, 0)
                            scale: Qt.vector3d(0.08, 1.38, 0.08)
                            castsShadows: true
                            materials: vintageLampMetalMat
                        }
                        // Wrist knuckle + hollow spun-metal shade + warm cream liner + parented SpotLight + volumetric cone
                        Node {
                            position: Qt.vector3d(0, 136, 0)
                            eulerRotation: Qt.vector3d(-36, 0, 56)

                            // Outer spun-metal vintage shade dome (hollow, open bottom with rolled rim)
                            Model {
                                geometry: lampShadeMesh
                                castsShadows: true; receivesShadows: true
                                materials: vintageLampMetalMat
                            }
                            // Warm cream-enamel inner reflector lining (lights up warmly when lamp is ON)
                            Model {
                                geometry: lampShadeInnerMesh
                                materials: PrincipledMaterial {
                                    baseColor: deskCtrl.deskLampOn ? "#fff1d0" : "#6a655a"
                                    emissiveFactor: deskCtrl.deskLampOn
                                        ? Qt.vector3d(1.45, 0.90, 0.36) : Qt.vector3d(0, 0, 0)
                                    roughness: 0.42
                                    metalness: 0.0
                                }
                            }
                            // Glowing vintage incandescent frosted bulb seated inside the hollow dome
                            Model {
                                source: "#Sphere"
                                position: Qt.vector3d(0, -18, 0)
                                scale: Qt.vector3d(0.24, 0.28, 0.24)
                                materials: PrincipledMaterial {
                                    baseColor: deskCtrl.deskLampOn ? "#fff0c8" : "#6c675e"
                                    emissiveFactor: deskCtrl.deskLampOn
                                        ? Qt.vector3d(2.8, 1.75, 0.68) : Qt.vector3d(0, 0, 0)
                                    roughness: 0.22
                                }
                            }
                            // Parented conical SpotLight emitting directly from the bulb along -Y (shade axis)
                            SpotLight {
                                id: benchLamp
                                visible: deskCtrl.deskLampOn
                                position: Qt.vector3d(0, -28, 0)
                                eulerRotation: Qt.vector3d(-90, 0, 0)
                                color: "#ff9e3d"
                                brightness: deskCtrl.fluorescentOn ? 0.55 : 4.60
                                coneAngle: 82
                                innerConeAngle: 36
                                constantFade: 0.28
                                linearFade: 0.16
                                quadraticFade: 0.85
                                castsShadow: true
                                shadowMapQuality: deskCtrl.textureTier === "2k" ? Light.ShadowMapQualityVeryHigh : Light.ShadowMapQualityHigh
                                softShadowQuality: deskCtrl.raytracingEnabled ? Light.PCF16 : Light.Hard
                                pcfFactor: 3.0
                                shadowBias: 0.85
                                shadowFactor: 90
                            }
                            // Quality-coupled subtle atmospheric volumetric tungsten beam cone (2K + Lamp-only mode)
                            Model {
                                visible: deskCtrl.deskLampOn && !deskCtrl.fluorescentOn && deskCtrl.textureTier === "2k"
                                geometry: lampConeMesh
                                castsShadows: false; receivesShadows: false
                                materials: CustomMaterial {
                                    shadingMode: CustomMaterial.Unshaded
                                    sourceBlend: CustomMaterial.SrcAlpha
                                    destinationBlend: CustomMaterial.One
                                    depthDrawMode: Material.NeverDepthDraw
                                    cullMode: Material.NoCulling
                                    property color coneColor: "#ffb058"
                                    property real coneIntensity: 0.075
                                    vertexShader: deskCtrl.shaderBaseUrl + "lamp_volumetric.vert"
                                    fragmentShader: deskCtrl.shaderBaseUrl + "lamp_volumetric.frag"
                                }
                            }
                        }
                    }
                }
            }
        }

        // === BIG RED INDUSTRIAL PUSHBUTTON (CONTROLS OVERHEAD FLUORESCENTS) ===
        Node {
            id: fluorescentButtonBox
            position: Qt.vector3d(-215, -130, 96)
            eulerRotation: Qt.vector3d(0, 18, 0)

            // Heavy industrial cast enclosure with 4 corner screws & yellow hazard collar
            Model {
                source: "#Cube"
                position: Qt.vector3d(0, 11, 0)
                scale: Qt.vector3d(0.46, 0.22, 0.46)
                castsShadows: true; receivesShadows: true
                materials: PrincipledMaterial {
                    baseColor: "#2e3133"
                    roughness: 0.60
                    metalness: 0.30
                }
            }
            Repeater3D {
                model: [
                    Qt.vector3d(-17.5, 22.1, -17.5), Qt.vector3d(17.5, 22.1, -17.5),
                    Qt.vector3d(-17.5, 22.1,  17.5), Qt.vector3d(17.5, 22.1,  17.5)
                ]
                Model {
                    geometry: screwMesh
                    position: modelData
                    eulerRotation.x: -90
                    scale: Qt.vector3d(0.72, 0.72, 0.72)
                    materials: brushedSteelMat
                }
            }
            Model {
                source: "#Cylinder"
                position: Qt.vector3d(0, 22.5, 0)
                scale: Qt.vector3d(0.37, 0.04, 0.37)
                materials: yellowPillMat
            }
            // Oversized glossy RED emergency/danger mushroom pushbutton
            Node {
                y: deskCtrl.fluorescentOn ? 26.5 : 32.0
                Behavior on y { NumberAnimation { duration: 95; easing.type: Easing.OutQuad } }
                Model {
                    objectName: "btn_fluorescent"
                    pickable: true
                    source: "#Cylinder"
                    scale: Qt.vector3d(0.30, 0.12, 0.30)
                    castsShadows: true
                    materials: PrincipledMaterial {
                        baseColor: "#d91b1b"
                        emissiveFactor: deskCtrl.fluorescentOn
                            ? Qt.vector3d(0.75, 0.08, 0.04) : Qt.vector3d(0.24, 0.02, 0.02)
                        roughness: 0.24
                        metalness: 0.0
                        specularAmount: 0.55
                    }
                }
                Model {
                    objectName: "btn_fluorescent"
                    pickable: true
                    source: "#Sphere"
                    y: 5.5
                    scale: Qt.vector3d(0.32, 0.11, 0.32)
                    castsShadows: true
                    materials: PrincipledMaterial {
                        baseColor: "#eb2222"
                        emissiveFactor: deskCtrl.fluorescentOn
                            ? Qt.vector3d(0.85, 0.10, 0.05) : Qt.vector3d(0.28, 0.03, 0.02)
                        roughness: 0.18
                        metalness: 0.0
                        specularAmount: 0.65
                        clearcoatAmount: 0.88
                        clearcoatRoughnessAmount: 0.12
                    }
                }
            }
        }

        // === INTERACTIVE 5-PAGE TATTERED LAB NOTEPAD (VOYNICH + REAL RECURRENCE MATH) ===
        // Positioned on the front-right of the bench in clear view; floats in front of camera on click.
        Node {
            id: labNotepad
            property vector3d targetPos: deskCtrl.notepadOpen
                ? Qt.vector3d(camera.position.x, camera.position.y - 12, camera.position.z - 182)
                : Qt.vector3d(182, -126.2, 118)
            property vector3d targetRot: deskCtrl.notepadOpen
                ? Qt.vector3d(camera.eulerRotation.x, camera.eulerRotation.y, 0)
                : Qt.vector3d(-90, -18, 0)
            property real targetScale: deskCtrl.notepadOpen ? 0.72 : 0.78

            position: targetPos
            eulerRotation: targetRot
            scale: Qt.vector3d(targetScale, targetScale, targetScale)

            Behavior on position { Vector3dAnimation { duration: 360; easing.type: Easing.OutCubic } }
            Behavior on eulerRotation { Vector3dAnimation { duration: 360; easing.type: Easing.OutCubic } }
            Behavior on scale { Vector3dAnimation { duration: 360; easing.type: Easing.OutCubic } }

            // Cardboard backing pad + stacked paper pages thickness
            Model {
                objectName: "notepad"
                pickable: true
                source: "#Cube"
                position: Qt.vector3d(0, 0, -2.2)
                scale: Qt.vector3d(0.98, 1.22, 0.04)
                castsShadows: !deskCtrl.notepadOpen
                receivesShadows: !deskCtrl.notepadOpen
                materials: PrincipledMaterial { baseColor: "#8c7a5b"; roughness: 0.92 }
            }
            // Active tattered paper page (unlit/gently readable when held up in dark room)
            Model {
                objectName: "notepad"
                pickable: true
                source: "#Rectangle"
                position: Qt.vector3d(0, 0, 0.2)
                scale: Qt.vector3d(0.96, 1.20, 1.0)
                materials: PrincipledMaterial {
                    lighting: deskCtrl.notepadOpen ? PrincipledMaterial.NoLighting : PrincipledMaterial.FragmentLighting
                    baseColorMap: notepadPageTex
                    roughness: 0.92
                    metalness: 0.0
                }
            }
        }

        // === LIVE VERLET-PHYSICS COAXIAL CABLES (CH1 X & CH2 Y) + PICKABLE BNC PLUGS ===
        Model {
            geometry: cableCh1Mesh
            castsShadows: true
            receivesShadows: true
            materials: darkRubberMat
        }
        Model {
            geometry: cableCh2Mesh
            castsShadows: true
            receivesShadows: true
            materials: darkRubberMat
        }
        // Pickable CH1 X BNC plug (machined knurled nickel collar + ribbed red elastomer strain-relief boot)
        Node {
            id: ch1PlugNode
            position: deskCtrl.plug1Pos
            Model {
                objectName: "plug_ch1"
                pickable: true
                geometry: bncPlugMesh
                castsShadows: true; receivesShadows: true
                materials: brushedSteelMat
            }
            Model {
                objectName: "plug_ch1"
                pickable: true
                geometry: strainBootMesh
                castsShadows: true; receivesShadows: true
                materials: PrincipledMaterial {
                    baseColor: "#c92a2a"
                    roughness: 0.46
                    clearcoatAmount: 0.28
                    clearcoatRoughnessAmount: 0.32
                }
            }
        }
        // Pickable CH2 Y BNC plug (machined knurled nickel collar + ribbed cobalt-blue elastomer strain-relief boot)
        Node {
            id: ch2PlugNode
            position: deskCtrl.plug2Pos
            Model {
                objectName: "plug_ch2"
                pickable: true
                geometry: bncPlugMesh
                castsShadows: true; receivesShadows: true
                materials: brushedSteelMat
            }
            Model {
                objectName: "plug_ch2"
                pickable: true
                geometry: strainBootMesh
                castsShadows: true; receivesShadows: true
                materials: PrincipledMaterial {
                    baseColor: "#2666c4"
                    roughness: 0.46
                    clearcoatAmount: 0.28
                    clearcoatRoughnessAmount: 0.32
                }
            }
        }

        // === C1-170 BENCH OSCILLOSCOPE RIG ===
        Node {
            id: scopeRig
            position: Qt.vector3d(0, 8, 0)
            eulerRotation: Qt.vector3d(-7.0, 0, 0)  // Propped up by the front tubular bail handle

            // Main enclosure body (438 x 214 x 272) — matte warm-beige ABS plastic
            Model {
                source: "#Cube"
                position: Qt.vector3d(0, 0, -136)
                scale: Qt.vector3d(4.36, 2.14, 2.70)
                castsShadows: true; receivesShadows: true
                materials: chassisPlasticMat
            }
            // Iconic C1-170 rounded-corner FRONT bezel frame — matte warm-beige ABS plastic
            Model {
                geometry: bezelFrameMesh
                position: Qt.vector3d(0, 0, -7.5)
                castsShadows: true; receivesShadows: true
                materials: chassisPlasticMat
            }
            // Matching rounded-corner REAR bumper frame — matte warm-beige ABS plastic
            Model {
                geometry: bezelFrameMesh
                position: Qt.vector3d(0, 0, -276)
                castsShadows: true; receivesShadows: true
                materials: chassisPlasticMat
            }

            // Peeling vintage 'CAL DUE: NOV 1989' calibration sticker seated flush on the upper faceplate
            Model {
                source: "#Rectangle"
                position: Qt.vector3d(-30, 89.2, 0.65)
                eulerRotation.z: -2.0
                scale: Qt.vector3d(0.31, 0.142, 1.0)
                receivesShadows: true
                materials: PrincipledMaterial {
                    baseColorMap: calStickerTex
                    alphaMode: PrincipledMaterial.Blend
                    roughness: 0.68
                    metalness: 0.0
                }
            }

            // === INTERACTIVE CARTOONISH SAM ALTMAN BOBBLEHEAD (X-ED OUT EYES) ON TOP OF SCOPE ===
            Node {
                id: bobbleheadRig
                position: Qt.vector3d(118, 107.2, -28)
                eulerRotation: Qt.vector3d(7.0, -12, 0)  // Level upright relative to the desk
                scale: Qt.vector3d(0.74, 0.74, 0.74)

                // Dark display pedestal base
                Model {
                    objectName: "bobblehead"
                    pickable: true
                    source: "#Cylinder"
                    position: Qt.vector3d(0, 3.5, 0)
                    scale: Qt.vector3d(0.38, 0.07, 0.38)
                    castsShadows: true
                    materials: PrincipledMaterial { baseColor: "#24262b"; roughness: 0.55 }
                }
                // Cartoon miniature legs + dark-grey hoodie/sweater torso
                Model {
                    objectName: "bobblehead"
                    pickable: true
                    source: "#Cylinder"
                    position: Qt.vector3d(-5.5, 13, 0)
                    scale: Qt.vector3d(0.08, 0.14, 0.08)
                    materials: PrincipledMaterial { baseColor: "#2c3440"; roughness: 0.80 }
                }
                Model {
                    objectName: "bobblehead"
                    pickable: true
                    source: "#Cylinder"
                    position: Qt.vector3d(5.5, 13, 0)
                    scale: Qt.vector3d(0.08, 0.14, 0.08)
                    materials: PrincipledMaterial { baseColor: "#2c3440"; roughness: 0.80 }
                }
                Model {
                    objectName: "bobblehead"
                    pickable: true
                    source: "#Cylinder"
                    position: Qt.vector3d(0, 30, 0)
                    scale: Qt.vector3d(0.24, 0.25, 0.19)
                    castsShadows: true
                    materials: PrincipledMaterial { baseColor: "#393e46"; roughness: 0.82 }
                }
                // True helical coiled steel spring neck
                Model {
                    geometry: bobbleSpringMesh
                    position: Qt.vector3d(0, 46, 0)
                    castsShadows: true
                    materials: brushedSteelMat
                }
                // Oversized spring-mounted bobblehead head with bold X-ed out eyes
                Node {
                    position: Qt.vector3d(0, 48, 0)
                    eulerRotation: Qt.vector3d(deskCtrl.bobbleRx, deskCtrl.bobbleRy, deskCtrl.bobbleRz)

                    Model {
                        objectName: "bobblehead"
                        pickable: true
                        source: "#Sphere"
                        position: Qt.vector3d(0, 17, 0)
                        eulerRotation.y: 0
                        scale: Qt.vector3d(0.36, 0.40, 0.35)
                        castsShadows: true
                        materials: PrincipledMaterial {
                            baseColorMap: bobbleFaceTex
                            roughness: 0.42
                            metalness: 0.0
                            clearcoatAmount: 0.45
                            clearcoatRoughnessAmount: 0.24
                        }
                    }
                    // Stylized cartoon hair tuft cap on top
                    Model {
                        objectName: "bobblehead"
                        pickable: true
                        source: "#Sphere"
                        position: Qt.vector3d(0, 33, -2)
                        scale: Qt.vector3d(0.30, 0.13, 0.28)
                        castsShadows: true
                        materials: PrincipledMaterial { baseColor: "#3b2a1e"; roughness: 0.78 }
                    }
                    // Cartoon ears
                    Model {
                        source: "#Sphere"
                        position: Qt.vector3d(-17.5, 16, 0)
                        scale: Qt.vector3d(0.07, 0.10, 0.06)
                        materials: PrincipledMaterial { baseColor: "#efc8a8"; roughness: 0.52 }
                    }
                    Model {
                        source: "#Sphere"
                        position: Qt.vector3d(17.5, 16, 0)
                        scale: Qt.vector3d(0.07, 0.10, 0.06)
                        materials: PrincipledMaterial { baseColor: "#efc8a8"; roughness: 0.52 }
                    }
                }
            }

            // Perforated circular-hole side ventilation grilles
            Model {
                source: "#Rectangle"
                position: Qt.vector3d(-218.5, 58, -206)
                eulerRotation.y: -90
                scale: Qt.vector3d(1.05, 0.42, 1.0)
                materials: PrincipledMaterial { baseColorMap: sideVentTex; roughness: 0.85; metalness: 0.0; specularAmount: 0.20 }
            }
            Model {
                source: "#Rectangle"
                position: Qt.vector3d(218.5, 58, -206)
                eulerRotation.y: 90
                scale: Qt.vector3d(1.05, 0.42, 1.0)
                materials: PrincipledMaterial { baseColorMap: sideVentTex; roughness: 0.85; metalness: 0.0; specularAmount: 0.20 }
            }

            // === ICONIC C1-170 SIDE-PIVOTED CONTINUOUS TUBULAR CARRY-HANDLE / BAIL STAND ===
            Node {
                id: carryHandle
                // Side pivot ratchet hubs
                Repeater3D {
                    model: [-221, 221]
                    Node {
                        position: Qt.vector3d(modelData, -8, -112)
                        Model { source: "#Cylinder"; eulerRotation.z: 90; scale: Qt.vector3d(0.68, 0.14, 0.68); castsShadows: true; receivesShadows: true; materials: handlePlasticMat }
                        Model { source: "#Cylinder"; position.x: modelData < 0 ? -9 : 9; eulerRotation.z: 90; scale: Qt.vector3d(0.46, 0.12, 0.46); castsShadows: true; receivesShadows: true; materials: handlePlasticMat }
                        Model { source: "#Cylinder"; position.x: modelData < 0 ? -15 : 15; eulerRotation.z: 90; scale: Qt.vector3d(0.18, 0.04, 0.18); materials: brushedSteelMat }
                    }
                }
                // Continuous smooth-bent tubular U-bail bar
                Model {
                    geometry: bailHandleMesh
                    castsShadows: true; receivesShadows: true
                    materials: handlePlasticMat
                }
                // Molded dark-rubber non-slip bench grip sleeves on the bail front bar
                Repeater3D {
                    model: [-168, 168]
                    Model {
                        source: "#Cylinder"
                        position: Qt.vector3d(modelData, -120, 24)
                        eulerRotation.z: 90
                        scale: Qt.vector3d(0.245, 0.56, 0.245)
                        castsShadows: true; receivesShadows: true
                        materials: darkRubberMat
                    }
                }
            }

            // 4 Machined slotted pan-head faceplate corner mounting screws
            Repeater3D {
                model: [
                    Qt.vector3d(-203, 93, 0.5),
                    Qt.vector3d(203, 93, 0.5),
                    Qt.vector3d(-203, -93, 0.5),
                    Qt.vector3d(203, -93, 0.5)
                ]
                Node {
                    position: modelData
                    eulerRotation.z: (index * 37 + 22)
                    Model {
                        geometry: screwMesh
                        castsShadows: true; receivesShadows: true
                        materials: brushedSteelMat
                    }
                    Model {
                        source: "#Cube"
                        position: Qt.vector3d(0, 0, 2.15)
                        scale: Qt.vector3d(0.056, 0.010, 0.008)
                        materials: darkRubberMat
                    }
                }
            }

            // Rubber rear feet
            Repeater3D {
                model: [Qt.vector3d(-176, -111, -226), Qt.vector3d(176, -111, -226)]
                Model { source: "#Cube"; position: modelData; scale: Qt.vector3d(0.26, 0.08, 0.22); materials: darkRubberMat }
            }

            // Two-tone C1-170 brushed METAL faceplate (ONLY metal panel on the scope)
            Model {
                source: "#Rectangle"
                position: Qt.vector3d(0, 0, 0.4)
                scale: Qt.vector3d(4.24, 2.02, 1.0)
                receivesShadows: true
                materials: PrincipledMaterial {
                    baseColorMap: faceplateAlbedoTex
                    normalMap: aluminumNormalTex
                    normalStrength: 0.16
                    roughnessMap: aluminumRoughnessTex
                    roughnessChannel: Material.R
                    emissiveMap: faceplateEmissiveTex
                    emissiveFactor: deskCtrl.lightingMode === 2
                        ? Qt.vector3d(0.11, 0.11, 0.11)
                        : (deskCtrl.lightingMode === 1 ? Qt.vector3d(0.04, 0.04, 0.04) : Qt.vector3d(0, 0, 0))
                    roughness: 0.44
                    metalness: 0.52
                }
            }

            // === LEFT BAY: MATTE BEIGE PLASTIC CRT HOOD + CURVED LIVE CRT GLASS + FLOATING GRATICULE GLASS ===
            Node {
                id: crtBay
                position: Qt.vector3d(-112, -2, 0.5)

                // True 13-tap live CRT texture area-light bounce shader + normal-mapped ABS grain on the CRT hood
                CustomMaterial {
                    id: crtHoodAreaLightMat
                    property TextureInput albedoMap: TextureInput { texture: chassisAlbedoTex }
                    property TextureInput normalMap: TextureInput { texture: chassisNormalTex }
                    property TextureInput roughnessMap: TextureInput { texture: chassisRoughnessTex }
                    property TextureInput crtTex: TextureInput { texture: crtLiveTex }
                    property color crtBgColor: deskCtrl.crtBgColor
                    property real bounceStrength: deskCtrl.lightingMode === 2 ? 2.15
                                                : (deskCtrl.lightingMode === 1 ? 1.25 : 0.44)
                    property real crtPower: deskCtrl.crtPowerLevel
                    vertexShader: deskCtrl.shaderBaseUrl + "crt_hood_bounce.vert"
                    fragmentShader: deskCtrl.shaderBaseUrl + "crt_hood_bounce.frag"
                }
                Model {
                    geometry: crtHoodMesh
                    castsShadows: true; receivesShadows: true
                    materials: deskCtrl.raytracingEnabled ? crtHoodAreaLightMat : chassisPlasticMat
                }
                // Fallback 4-point inner-bezel lights (used only when Hybrid RT is toggled OFF)
                PointLight {
                    visible: !deskCtrl.raytracingEnabled
                    position: Qt.vector3d(-42, deskCtrl.crtCentroidY * 28, 16)
                    color: deskCtrl.crtColor
                    brightness: view3d.bezelBounceGain * deskCtrl.crtLeftBounce * deskCtrl.crtPowerLevel
                    constantFade: 0.45; linearFade: 1.1; quadraticFade: 7.5; castsShadow: false
                }
                PointLight {
                    visible: !deskCtrl.raytracingEnabled
                    position: Qt.vector3d(42, deskCtrl.crtCentroidY * 28, 16)
                    color: deskCtrl.crtColor
                    brightness: view3d.bezelBounceGain * deskCtrl.crtRightBounce * deskCtrl.crtPowerLevel
                    constantFade: 0.45; linearFade: 1.1; quadraticFade: 7.5; castsShadow: false
                }
                PointLight {
                    visible: !deskCtrl.raytracingEnabled
                    position: Qt.vector3d(deskCtrl.crtCentroidX * 30, 38, 16)
                    color: deskCtrl.crtColor
                    brightness: view3d.bezelBounceGain * deskCtrl.crtTopBounce * deskCtrl.crtPowerLevel
                    constantFade: 0.45; linearFade: 1.1; quadraticFade: 7.5; castsShadow: false
                }
                PointLight {
                    visible: !deskCtrl.raytracingEnabled
                    position: Qt.vector3d(deskCtrl.crtCentroidX * 30, -38, 16)
                    color: deskCtrl.crtColor
                    brightness: view3d.bezelBounceGain * deskCtrl.crtBottomBounce * deskCtrl.crtPowerLevel
                    constantFade: 0.45; linearFade: 1.1; quadraticFade: 7.5; castsShadow: false
                }
                // Unlit CRT phosphor glass backing (visible behind the collapsing trace during power-off)
                Model {
                    geometry: crtMesh
                    position: Qt.vector3d(0, 0, 1.9)
                    materials: PrincipledMaterial {
                        lighting: PrincipledMaterial.NoLighting
                        baseColor: deskCtrl.crtBgColor
                    }
                }
                // Self-luminous curved CRT phosphor layer (scales down to a line/dot during PWR collapse/warm-up)
                Model {
                    id: crtGlass
                    geometry: crtMesh
                    position: Qt.vector3d(0, 0, 2.2)
                    scale: Qt.vector3d(deskCtrl.crtScaleX, deskCtrl.crtScaleY, 1.0)
                    visible: deskCtrl.crtPowerLevel > 0.01
                    materials: PrincipledMaterial {
                        lighting: PrincipledMaterial.NoLighting
                        baseColorMap: crtLiveTex
                    }
                }
                // Blazing analog CRT power-collapse / warm-up center phosphor dot
                Model {
                    id: crtPowerDot
                    source: "#Sphere"
                    position: Qt.vector3d(0, 0, 12.8)
                    scale: Qt.vector3d(0.08 * deskCtrl.crtDotGlow, 0.08 * deskCtrl.crtDotGlow, 0.02)
                    visible: deskCtrl.crtDotGlow > 0.01
                    materials: PrincipledMaterial {
                        baseColor: "#ffffff"
                        emissiveFactor: Qt.vector3d(
                            (0.8 + deskCtrl.crtColor.r) * 3.0 * deskCtrl.crtDotGlow,
                            (0.8 + deskCtrl.crtColor.g) * 3.0 * deskCtrl.crtDotGlow,
                            (0.8 + deskCtrl.crtColor.b) * 3.0 * deskCtrl.crtDotGlow
                        )
                    }
                }
                // Floating thick cover-glass with physical Schlick Fresnel, live PBR reflections,
                // and crisp analytic etched graticule with edge-lit phosphor scatter (true glass-thickness parallax!)
                CustomMaterial {
                    id: crtCoverGlassMat
                    shadingMode: CustomMaterial.Shaded
                    sourceBlend: CustomMaterial.One
                    destinationBlend: CustomMaterial.OneMinusSrcAlpha
                    depthDrawMode: Material.NeverDepthDraw
                    property TextureInput crtTex: TextureInput { texture: crtLiveTex }
                    property color crtBgColor: deskCtrl.crtBgColor
                    property real showGrid: deskCtrl.showGrid ? 1.0 : 0.0
                    property real crtPower: deskCtrl.crtPowerLevel
                    property real ambientLevel: deskCtrl.lightingMode === 0 ? 1.0
                                              : (deskCtrl.lightingMode === 1 ? 0.55 : 0.12)
                    property real reflectionGain: deskCtrl.lightingMode === 0 ? 1.0
                                                : (deskCtrl.lightingMode === 1 ? 0.82 : 0.45)
                    Behavior on ambientLevel { NumberAnimation { duration: 380 } }
                    Behavior on reflectionGain { NumberAnimation { duration: 380 } }
                    vertexShader: deskCtrl.shaderBaseUrl + "crt_cover_glass.vert"
                    fragmentShader: deskCtrl.shaderBaseUrl + "crt_cover_glass.frag"
                }
                Model {
                    id: crtCoverGlass
                    geometry: crtMesh
                    position: Qt.vector3d(0, 0, 4.4)
                    materials: crtCoverGlassMat
                }
            }

            // === REUSABLE C1-170 3D CONTROLS ===
            component TrimKnob3D: Node {
                property string knobId: ""
                property real normValue: 0.5
                Model {
                    objectName: knobId
                    pickable: true
                    geometry: trimKnobMesh
                    eulerRotation.z: 135.0 - normValue * 270.0
                    castsShadows: true; receivesShadows: true
                    materials: lightKnobMat
                    Model { source: "#Cylinder"; position: Qt.vector3d(0, 0, 12.8); eulerRotation.x: 90; scale: Qt.vector3d(0.11, 0.012, 0.11); materials: brushedSteelMat }
                    Model { source: "#Cube"; position: Qt.vector3d(0, 3.6, 13.4); scale: Qt.vector3d(0.016, 0.055, 0.012); materials: darkPointerMat }
                }
            }

            component SelectorKnob3D: Node {
                property string knobId: ""
                property real normValue: 0.5
                Model {
                    objectName: knobId
                    pickable: true
                    geometry: knobMesh
                    eulerRotation.z: 135.0 - normValue * 270.0
                    castsShadows: true; receivesShadows: true
                    materials: lightKnobMat
                    Model { source: "#Cylinder"; position: Qt.vector3d(0, 0, 19.3); eulerRotation.x: 90; scale: Qt.vector3d(0.12, 0.012, 0.12); materials: brushedSteelMat }
                    Model { source: "#Cube"; position: Qt.vector3d(0, 5.2, 19.8); scale: Qt.vector3d(0.020, 0.085, 0.016); materials: darkPointerMat }
                }
            }

            component PillButton3D: Node {
                property string btnId: ""
                property bool active: false
                property var capMaterial: greyPillMat
                Model { source: "#Cube"; position: Qt.vector3d(0, 0, 0.5); scale: Qt.vector3d(0.17, 0.095, 0.02); materials: darkRubberMat }
                Model {
                    objectName: btnId
                    pickable: btnId.length > 0
                    geometry: keycapMesh
                    position.z: active ? 0.9 : 4.2
                    Behavior on position.z { NumberAnimation { duration: 90; easing.type: Easing.OutQuad } }
                    castsShadows: true; receivesShadows: true
                    materials: capMaterial
                }
            }

            // 10-13: 4 LED-equipped pill pushbuttons (each active LED casts its own matching local PointLight)
            component LedPillButton3D: Node {
                property string btnId: ""
                property bool active: false
                property bool ledLit: active
                property var capMaterial: greenPillMat
                property color ledColor: "#38f068"
                Model { source: "#Cube"; position: Qt.vector3d(0, 0, 0.5); scale: Qt.vector3d(0.17, 0.095, 0.02); materials: darkRubberMat }
                Model {
                    objectName: btnId
                    pickable: btnId.length > 0
                    geometry: keycapMesh
                    position.z: active ? 0.9 : 4.2
                    Behavior on position.z { NumberAnimation { duration: 90; easing.type: Easing.OutQuad } }
                    castsShadows: true; receivesShadows: true
                    materials: capMaterial
                }
                Model {
                    source: "#Sphere"
                    position: Qt.vector3d(0, 6.8, 1.8)
                    scale: Qt.vector3d(0.028, 0.028, 0.024)
                    materials: PrincipledMaterial {
                        baseColor: (ledLit && deskCtrl.crtPowered) ? ledColor : "#2c3029"
                        emissiveFactor: (ledLit && deskCtrl.crtPowered)
                            ? Qt.vector3d(ledColor.r * 1.15, ledColor.g * 1.15, ledColor.b * 1.15)
                            : Qt.vector3d(0, 0, 0)
                        roughness: 0.18
                    }
                }
                PointLight {
                    visible: ledLit && deskCtrl.crtPowered && !deskCtrl.fluorescentOn
                    position: Qt.vector3d(0, 7.6, 5.5)
                    color: ledColor
                    brightness: deskCtrl.lightingMode === 2 ? 0.55 : 0.24
                    constantFade: 0.35; linearFade: 1.8; quadraticFade: 8.5
                    castsShadow: false
                }
            }

            // Narrow vertical CRT trim column
            TrimKnob3D { knobId: "knob_intensity"; position: Qt.vector3d(-2, 42, 0.5);  normValue: deskCtrl.knobIntensity }
            TrimKnob3D { knobId: "knob_gain";      position: Qt.vector3d(-2, -2, 0.5);  normValue: deskCtrl.knobGain }
            TrimKnob3D { knobId: "knob_output";    position: Qt.vector3d(-2, -46, 0.5); normValue: deskCtrl.knobOutput }

            // Top-right utility strip: LIGHT (yellow), PLAY (green LED), BEAM COLOR (beam LED), GRID (green LED)
            PillButton3D    { btnId: "btn_light"; position: Qt.vector3d(34, 76, 0.5);  active: deskCtrl.lightingMode > 0; capMaterial: yellowPillMat }
            LedPillButton3D { btnId: "btn_play";  position: Qt.vector3d(68, 76, 0.5);  active: deskCtrl.playing;          capMaterial: greenPillMat; ledColor: "#38f068" }
            LedPillButton3D { btnId: "btn_beam";  position: Qt.vector3d(94, 76, 0.5);  active: false; ledLit: true;       capMaterial: greenPillMat; ledColor: deskCtrl.crtColor }
            LedPillButton3D { btnId: "btn_grid";  position: Qt.vector3d(120, 76, 0.5); active: deskCtrl.showGrid;         capMaterial: greenPillMat; ledColor: "#38f068" }

            // 3 Recessed circular test banana sockets
            Repeater3D {
                model: [154, 174, 194]
                Node {
                    position: Qt.vector3d(modelData, 78, 0.7)
                    Model { source: "#Cylinder"; eulerRotation.x: 90; scale: Qt.vector3d(0.085, 0.02, 0.085); materials: brushedSteelMat }
                    Model { source: "#Cylinder"; position.z: 1.0; eulerRotation.x: 90; scale: Qt.vector3d(0.055, 0.02, 0.055); materials: darkRubberMat }
                }
            }

            // Upper-middle pill switch row
            PillButton3D { btnId: "";            position: Qt.vector3d(26, 36, 0.5);  capMaterial: greyPillMat }
            PillButton3D { btnId: "btn_object";  position: Qt.vector3d(48, 36, 0.5);  capMaterial: greyPillMat }
            PillButton3D { btnId: "";            position: Qt.vector3d(76, 36, 0.5);  capMaterial: greyPillMat }
            PillButton3D { btnId: "btn_palette"; position: Qt.vector3d(104, 36, 0.5); capMaterial: greyPillMat }
            PillButton3D { btnId: "";            position: Qt.vector3d(154, 36, 0.5); capMaterial: greyPillMat }
            PillButton3D { btnId: "";            position: Qt.vector3d(182, 36, 0.5); capMaterial: greyPillMat }

            // Reusable recessed vintage LCD readout panel mounted beneath each selector knob (with TN digit shadow + glass lens)
            component LcdReadout3D: Node {
                property string readoutText: ""
                property bool dimmed: false
                Model { source: "#Cube"; position: Qt.vector3d(0, 0, 0.5); scale: Qt.vector3d(0.365, 0.128, 0.026); materials: darkRubberMat }
                Model {
                    source: "#Rectangle"
                    position: Qt.vector3d(0, 0, 1.85)
                    scale: Qt.vector3d(0.32, 0.095, 1.0)
                    materials: PrincipledMaterial {
                        lighting: PrincipledMaterial.NoLighting
                        baseColorMap: Texture {
                            sourceItem: Rectangle {
                                width: 160; height: 48
                                color: deskCtrl.lightingMode === 2
                                    ? (dimmed ? "#0b140d" : "#132618")
                                    : (deskCtrl.lightingMode === 1
                                        ? (dimmed ? "#223024" : "#334d36")
                                        : (dimmed ? "#7a8774" : "#8ba37c"))
                                border.color: "#1b241c"; border.width: 3
                                // Twisted-nematic LCD subtle substrate drop-shadow behind active digits
                                Text {
                                    anchors.centerIn: parent
                                    anchors.horizontalCenterOffset: 2
                                    anchors.verticalCenterOffset: 2
                                    visible: deskCtrl.lightingMode === 0
                                    text: readoutText
                                    font.family: "JetBrains Mono"; font.pixelSize: 24; font.bold: true
                                    color: "#2a0e190e"
                                }
                                Text {
                                    anchors.centerIn: parent
                                    text: readoutText
                                    font.family: "JetBrains Mono"; font.pixelSize: 24; font.bold: true
                                    color: deskCtrl.lightingMode === 2
                                        ? (dimmed ? "#2d5936" : "#6af287")
                                        : (deskCtrl.lightingMode === 1
                                            ? (dimmed ? "#5b7a5e" : "#9ff0a8")
                                            : (dimmed ? "#485445" : "#141f14"))
                                }
                            }
                        }
                    }
                }
                // Glossy protective clear glass cover lens catching subtle room reflections
                Model {
                    source: "#Rectangle"
                    position: Qt.vector3d(0, 0, 2.05)
                    scale: Qt.vector3d(0.32, 0.095, 1.0)
                    materials: PrincipledMaterial {
                        baseColor: "#14ffffff"
                        alphaMode: PrincipledMaterial.Blend
                        depthDrawMode: Material.NeverDepthDraw
                        roughness: 0.08
                        metalness: 0.0
                        clearcoatAmount: 0.90
                        clearcoatRoughnessAmount: 0.06
                    }
                }
            }

            // 3 Large dual-concentric selector knobs: CH1 X GAIN, CH2 Y GAIN, TIME WINDOW
            SelectorKnob3D { knobId: "knob_gain_x";   position: Qt.vector3d(48, -8, 0.5);  normValue: deskCtrl.knobGainX }
            SelectorKnob3D { knobId: "knob_gain_y";   position: Qt.vector3d(104, -8, 0.5); normValue: deskCtrl.knobGainY }
            SelectorKnob3D { knobId: "knob_time_win"; position: Qt.vector3d(170, -8, 0.5); normValue: deskCtrl.knobTimeWin }

            // Little recessed LCD readouts directly beneath each selector knob
            LcdReadout3D { position: Qt.vector3d(48, -34, 0.6);  readoutText: deskCtrl.lcdGainX; dimmed: !deskCtrl.crtPowered || !deskCtrl.ch1Plugged }
            LcdReadout3D { position: Qt.vector3d(104, -34, 0.6); readoutText: deskCtrl.lcdGainY; dimmed: !deskCtrl.crtPowered || !deskCtrl.ch2Plugged }
            LcdReadout3D { position: Qt.vector3d(170, -34, 0.6); readoutText: deskCtrl.lcdTimeWin; dimmed: !deskCtrl.crtPowered || !deskCtrl.sweepMode }

            // Lower-row pill switches: X-Y / TIME directly under CH1 X GAIN
            LedPillButton3D { btnId: "btn_view"; position: Qt.vector3d(48, -52, 0.5); active: deskCtrl.sweepMode; capMaterial: greyPillMat; ledColor: "#ffa826" }
            Repeater3D {
                model: [92, 118, 154, 182]
                PillButton3D { position: Qt.vector3d(modelData, -52, 0.5); capMaterial: greyPillMat }
            }

            // Sparse, non-periodic physical wear decals on the beige bezels
            Model {
                source: "#Rectangle"; position: Qt.vector3d(-196, -104, 12.75); eulerRotation.z: 14; scale: Qt.vector3d(0.42, 0.16, 1.0)
                materials: PrincipledMaterial { baseColorMap: bezelSmudgeTex; alphaMode: PrincipledMaterial.Blend; roughness: 0.72; metalness: 0.0 }
            }
            Model {
                source: "#Rectangle"; position: Qt.vector3d(216, -28, 12.75); eulerRotation.z: 82; scale: Qt.vector3d(0.36, 0.11, 1.0)
                materials: PrincipledMaterial { baseColorMap: bezelSmudgeTex; alphaMode: PrincipledMaterial.Blend; roughness: 0.72; metalness: 0.0 }
            }
            Model {
                source: "#Rectangle"; position: Qt.vector3d(-44, -78, 18.75); eulerRotation.z: -9; scale: Qt.vector3d(0.34, 0.12, 1.0)
                materials: PrincipledMaterial { baseColorMap: bezelSmudgeTex; alphaMode: PrincipledMaterial.Blend; roughness: 0.74; metalness: 0.0 }
            }
            Model {
                source: "#Rectangle"; position: Qt.vector3d(138, 105, 12.75); eulerRotation.z: -6; scale: Qt.vector3d(0.24, 0.10, 1.0)
                materials: PrincipledMaterial { baseColorMap: bezelGougeTex; alphaMode: PrincipledMaterial.Blend; roughness: 0.88; metalness: 0.0 }
            }
            Model {
                source: "#Rectangle"; position: Qt.vector3d(-216, 26, 12.75); eulerRotation.z: 74; scale: Qt.vector3d(0.20, 0.09, 1.0)
                materials: PrincipledMaterial { baseColorMap: bezelGougeTex; alphaMode: PrincipledMaterial.Blend; roughness: 0.88; metalness: 0.0 }
            }
            Model {
                source: "#Rectangle"; position: Qt.vector3d(-176, 75, 18.75); eulerRotation.z: 18; scale: Qt.vector3d(0.18, 0.09, 1.0)
                materials: PrincipledMaterial { baseColorMap: bezelGougeTex; alphaMode: PrincipledMaterial.Blend; roughness: 0.88; metalness: 0.0 }
            }

            // === BOTTOM DARK-CHARCOAL STRIP: 3 MACHINED BNC JACKS + WORKING YELLOW PWR BUTTON ===
            Repeater3D {
                model: [{ x: 46, id: "bnc_ch1" }, { x: 102, id: "bnc_ch2" }, { x: 152, id: "" }]
                Node {
                    position: Qt.vector3d(modelData.x, -84, 0.6)
                    Model {
                        objectName: modelData.id
                        pickable: modelData.id.length > 0
                        geometry: bncMesh
                        castsShadows: true; receivesShadows: true
                        materials: brushedSteelMat
                    }
                    Model {
                        source: "#Cylinder"; position.z: 13.0; eulerRotation.x: 90
                        scale: Qt.vector3d(0.068, 0.015, 0.068)
                        materials: PrincipledMaterial { baseColor: "#e6e2d8"; roughness: 0.45 }
                    }
                    Model {
                        source: "#Cylinder"; position.z: 13.6; eulerRotation.x: 90
                        scale: Qt.vector3d(0.018, 0.02, 0.018)
                        materials: PrincipledMaterial { baseColor: "#e8c455"; roughness: 0.22; metalness: 0.92 }
                    }
                }
            }
            // Working round yellow MAINS / PWR button in bottom-right corner
            Node {
                position: Qt.vector3d(192, -84, 0.6)
                Model { source: "#Cylinder"; eulerRotation.x: 90; scale: Qt.vector3d(0.13, 0.03, 0.13); materials: brushedSteelMat }
                Model {
                    objectName: "btn_power"
                    pickable: true
                    source: "#Cylinder"
                    position.z: deskCtrl.crtPowered ? 3.2 : 5.4
                    Behavior on position.z { NumberAnimation { duration: 95; easing.type: Easing.OutQuad } }
                    eulerRotation.x: 90
                    scale: Qt.vector3d(0.092, 0.09, 0.092)
                    castsShadows: true
                    materials: PrincipledMaterial {
                        baseColor: deskCtrl.crtPowered ? "#ebb91e" : "#87712b"
                        emissiveFactor: deskCtrl.crtPowered
                            ? (deskCtrl.lightingMode === 2 ? Qt.vector3d(0.55, 0.40, 0.06) : Qt.vector3d(0.12, 0.08, 0.01))
                            : Qt.vector3d(0, 0, 0)
                        roughness: 0.26
                    }
                }
            }
        }
    }

    // === 3D PICKING: KNOBS, BUTTONS, CABLES, NOTEPAD, BOBBLEHEAD, LAMP SWITCH, RED BUTTON ===
    MouseArea {
        id: pointerArea
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton

        property string dragKnob: ""
        property int dragCableIdx: -1
        property real dragStartX: 0.0
        property real dragStartY: 0.0
        property real dragStartVal: 0.5

        onPressed: function(mouse) {
            root.forceActiveFocus()
            var hit = view3d.pick(mouse.x, mouse.y)
            if (hit && hit.objectHit && hit.objectHit.objectName) {
                var name = hit.objectHit.objectName
                if (name === "notepad") {
                    deskCtrl.clickNotepad()
                    return
                }
                if (name === "bobblehead") {
                    deskCtrl.pokeBobblehead()
                    return
                }
                if (name === "plug_ch1" || name === "plug_ch2") {
                    dragCableIdx = (name === "plug_ch1") ? 0 : 1
                    dragStartX = mouse.x
                    dragStartY = mouse.y
                    deskCtrl.grabCable(dragCableIdx)
                    return
                }
                if (name === "bnc_ch1" && !deskCtrl.ch1Plugged) {
                    deskCtrl.replugPort(0)
                    return
                }
                if (name === "bnc_ch2" && !deskCtrl.ch2Plugged) {
                    deskCtrl.replugPort(1)
                    return
                }
                if (name.indexOf("knob_") === 0) {
                    dragKnob = name
                    dragStartY = mouse.y
                    dragStartVal = deskCtrl.getKnob(name)
                    return
                }
                if (name.indexOf("btn_") === 0) {
                    deskCtrl.pressButton(name)
                    return
                }
            }
            if (deskCtrl.notepadOpen) {
                deskCtrl.closeNotepad()
            }
            dragKnob = ""
            dragCableIdx = -1
        }

        onPositionChanged: function(mouse) {
            if (dragCableIdx >= 0) {
                deskCtrl.dragCable(dragCableIdx, mouse.x - dragStartX, mouse.y - dragStartY)
                return
            }
            if (dragKnob !== "") {
                var delta = (dragStartY - mouse.y) / 180.0
                var next = Math.max(0.0, Math.min(1.0, dragStartVal + delta))
                deskCtrl.setKnob(dragKnob, next)
                return
            }
            if (!deskCtrl.cameraFixed) {
                root.parallaxX = (mouse.x / Math.max(1, width) - 0.5) * 2.0
                root.parallaxY = (mouse.y / Math.max(1, height) - 0.5) * 2.0
            }
        }

        onDoubleClicked: function(mouse) {
            var hit = view3d.pick(mouse.x, mouse.y)
            if (hit && hit.objectHit && hit.objectHit.objectName) {
                var name = hit.objectHit.objectName
                if (name.indexOf("knob_") === 0) {
                    dragKnob = ""
                    deskCtrl.resetKnob(name)
                } else if (name === "plug_ch1" && !deskCtrl.ch1Plugged) {
                    deskCtrl.replugCable(0)
                } else if (name === "plug_ch2" && !deskCtrl.ch2Plugged) {
                    deskCtrl.replugCable(1)
                }
            }
        }

        onReleased: {
            if (dragCableIdx >= 0) {
                deskCtrl.releaseCable(dragCableIdx)
                dragCableIdx = -1
            }
            dragKnob = ""
        }
        onExited: {
            if (dragKnob === "" && dragCableIdx < 0 && !deskCtrl.cameraFixed) {
                root.parallaxX = 0.0
                root.parallaxY = 0.0
            }
        }
    }

    // === MINIMAL FLOATING HUD BAR (FOCUS PRESETS + CAMERA FIX + EXIT) ===
    Rectangle {
        id: hudBar
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 16
        height: 36
        width: hudRow.implicitWidth + 24
        radius: 6
        color: "#cc191815"
        border.color: "#55686254"
        border.width: 1

        Row {
            id: hudRow
            anchors.centerIn: parent
            spacing: 8

            component HudPill: Rectangle {
                property string label: ""
                property bool selected: false
                signal triggered()
                height: 24
                width: txt.implicitWidth + 16
                radius: 4
                color: selected ? "#217a3e" : (pillMa.containsMouse ? "#38352d" : "#26241f")
                border.color: selected ? "#4ef278" : "#4f4a3e"
                border.width: 1
                Text {
                    id: txt
                    anchors.centerIn: parent
                    text: label
                    color: selected ? "#ffffff" : "#ddd7c5"
                    font.family: "JetBrains Mono"
                    font.pixelSize: 11
                    font.bold: selected
                }
                MouseArea {
                    id: pillMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: parent.triggered()
                }
            }

            HudPill { label: "1 Desk";       selected: deskCtrl.cameraPreset === 0; onTriggered: deskCtrl.setCameraPreset(0) }
            HudPill { label: "2 Scope";      selected: deskCtrl.cameraPreset === 1; onTriggered: deskCtrl.setCameraPreset(1) }
            HudPill { label: "3 Controls";   selected: deskCtrl.cameraPreset === 2; onTriggered: deskCtrl.setCameraPreset(2) }

            Rectangle { width: 1; height: 18; anchors.verticalCenter: parent.verticalCenter; color: "#4a463c" }

            HudPill { label: "F · Camera Fix"; selected: deskCtrl.cameraFixed; onTriggered: deskCtrl.toggleCameraFix() }
            HudPill { label: "R · Hybrid RT";  selected: deskCtrl.raytracingEnabled; onTriggered: deskCtrl.toggleRaytracing() }
            HudPill { visible: deskCtrl.pathTraceAvailable; label: "T · Path Trace"; selected: deskCtrl.pathTraceActive; onTriggered: deskCtrl.togglePathTrace() }

            Rectangle { width: 1; height: 18; anchors.verticalCenter: parent.verticalCenter; color: "#4a463c" }

            HudPill { label: "Esc · 2D Editor"; selected: false; onTriggered: deskCtrl.exit3D() }
        }
    }

    // Live hardware parameter readout pill
    Rectangle {
        visible: deskCtrl.hudReadout.length > 0
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 14
        height: 28
        width: readoutTxt.implicitWidth + 24
        radius: 4
        color: "#d8111a12"
        border.color: "#38a856"
        border.width: 1
        Text {
            id: readoutTxt
            anchors.centerIn: parent
            text: deskCtrl.hudReadout
            color: "#8ff08a"
            font.family: "JetBrains Mono"
            font.pixelSize: 12
            font.bold: true
        }
    }
}
