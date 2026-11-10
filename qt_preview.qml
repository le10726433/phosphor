import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Oscilloscope 1.0

ApplicationWindow {
    id: win
    width: 1180; height: 820
    minimumWidth: 960; minimumHeight: 680
    visible: true
    title: "Oscilloscope Music — Qt GPU experiment"
    color: control.colors.bg
    font.family: "JetBrains Mono"
    palette.window: control.colors.bg
    palette.base: control.colors.field
    palette.button: control.colors.field
    palette.buttonText: control.colors.fg
    palette.text: control.colors.fg
    palette.windowText: control.colors.fg
    palette.highlight: control.colors.accent
    palette.highlightedText: control.colors.bg
    RowLayout {
        anchors.fill: parent; anchors.margins: 20; spacing: 20
        ScrollView {
            Layout.preferredWidth: 300; Layout.fillHeight: true
            contentWidth: availableWidth
            ColumnLayout {
                width: 280; spacing: 10
                Label { text: "OSCILLOSCOPE / GPU"; color: control.colors.accent; font.bold: true; font.pixelSize: 18 }
                Label { text: "WINDOWS PROTOTYPE · 0.4.0"; color: control.colors.muted }
                Button { text: "OPEN SCENE…"; Layout.fillWidth: true; onClicked: control.openScene() }
                Label { text: "INPUT"; color: control.colors.accent }
                ComboBox { Layout.fillWidth: true; model: control.sources; currentIndex: model.indexOf(control.values.source); onActivated: control.setValue("source", currentText) }
                Label { text: "Quarter notes / BPM" }
                SpinBox { from: 20; to: 300; value: control.values.bpm; editable: true; onValueModified: control.setValue("bpm", value) }
                Button { text: "PLAY / STOP AUDIO"; Layout.fillWidth: true; onClicked: control.play() }
                Label { text: "SCENE"; color: control.colors.accent }
                Label { visible: control.values.loaded; text: "Imported multi-shape scene.\nShape/view edits replace it with one solid."; wrapMode: Text.WordWrap; Layout.fillWidth: true; color: control.colors.muted }
                ComboBox { Layout.fillWidth: true; model: control.solids; currentIndex: model.indexOf(control.values.solid); onActivated: control.setValue("solid", currentText) }
                Label { text: "Rotation: " + (control.values.rotation >= 100 ? "∞ (stopped)" : Math.pow(10,control.values.rotation/20-2).toFixed(2)+" s/revolution") }
                Slider { Layout.fillWidth: true; from: 0; to: 100; value: control.values.rotation; onMoved: control.setValue("rotation", value) }
                RowLayout {
                    Repeater {
                        model: ["x", "y", "z"]
                        ColumnLayout {
                            required property string modelData
                            Label { text: modelData.toUpperCase()+"°" }
                            SpinBox { from: -180; to: 180; value: control.values[parent.modelData]; editable: true; Layout.preferredWidth: 86; onValueModified: control.setValue(parent.modelData, value) }
                        }
                    }
                }
                Label { text: "MIX"; color: control.colors.accent }
                ComboBox { Layout.fillWidth: true; model: ["Processed output", "Fully shaped audio", "Original audio"]; currentIndex: ["mixed","drawing","music"].indexOf(control.values.mode); onActivated: control.setValue("mode", ["mixed","drawing","music"][currentIndex]) }
                Label { text: "Output level: "+control.values.gain.toFixed(2) }
                Slider { Layout.fillWidth: true; from: 0; to: .95; value: control.values.gain; onMoved: control.setValue("gain", value) }
                Label { text: "Original blend: "+Math.round(control.values.blend*100)+"%" }
                Slider { Layout.fillWidth: true; from: 0; to: 1; value: control.values.blend; onMoved: control.setValue("blend", value) }
                Label { text: "APPEARANCE (preview only)"; color: control.colors.accent }
                ComboBox { Layout.fillWidth: true; model: control.palettes; currentIndex: model.indexOf(control.paletteName); onActivated: control.setValue("palette",currentText) }
                CheckBox { text: "GPU trace glow"; checked: control.values.glow; onToggled: control.setValue("glow",checked) }
                Label { text: "Experiment only. Use app.py for the\nfull editor, MIDI setup and recording."; color: control.colors.muted; wrapMode: Text.WordWrap }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            Label { text: "X–Y SIGNAL MONITOR"; color: control.colors.accent; font.bold: true }
            Item {
                Layout.fillWidth: true; Layout.fillHeight: true
                Rectangle {
                    width: Math.min(parent.width,parent.height); height: width
                    anchors.centerIn: parent; color: control.colors.background
                    border.color: control.colors.border
                    GpuScope { objectName: "scope"; anchors.fill: parent; anchors.margins: 1 }
                    Label { anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 12; text: "L → X     R → Y     48 kHz"; color: control.colors.muted; font.pixelSize: 11 }
                }
            }
            Label { text: control.status; color: control.colors.accent; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: control.backend; color: control.colors.muted; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 11 }
        }
    }
}
