// SPDX-License-Identifier: AGPL-3.0-or-later
import AppKit
import Vision

typealias Row = [String: Any]

@MainActor
final class Picker: NSObject {
    let field: NSTextField
    let mode: String
    init(_ field: NSTextField, _ mode: String) { self.field = field; self.mode = mode }
    @objc func pick() {
        if mode == "folder" || mode == "file" {
            let panel = NSOpenPanel()
            panel.canChooseDirectories = mode == "folder"
            panel.canChooseFiles = mode == "file"
            panel.allowsMultipleSelection = false
            if panel.runModal() == .OK { field.stringValue = panel.url?.path ?? "" }
        } else {
            let panel = NSSavePanel()
            panel.nameFieldStringValue = (field.stringValue as NSString).lastPathComponent
            if panel.runModal() == .OK { field.stringValue = panel.url?.path ?? "" }
        }
    }
}

@main
struct Panel {
    @MainActor static func main() throws {
        if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "ocr" {
            let req = VNRecognizeTextRequest()
            req.recognitionLevel = .accurate
            req.usesLanguageCorrection = true
            req.recognitionLanguages = ["zh-Hans", "en-US"]
            try VNImageRequestHandler(url: URL(fileURLWithPath: CommandLine.arguments[2])).perform([req])
            let rows: [Row] = (req.results ?? []).compactMap { item in
                guard let hit = item.topCandidates(1).first else { return nil }
                let b = item.boundingBox
                return ["text": hit.string, "box": [b.minX,b.minY,b.width,b.height]]
            }
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: rows))
            return
        }
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let form = try JSONSerialization.jsonObject(with: input) as? Row else { exit(2) }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let alert = NSAlert()
        alert.messageText = form["title"] as? String ?? "HP 115nw"
        alert.informativeText = form["detail"] as? String ?? ""
        var captures: [String: () -> String] = [:]
        var pickers: [Picker] = []
        let tabs = NSTabView(frame: NSRect(x:0,y:0,width:520,height:350))
        let groups = form["tabs"] as? [Row] ?? []
        for group in groups {
            let item = NSTabViewItem()
            item.label = group["name"] as? String ?? ""
            let fields = group["rows"] as? [Row] ?? []
            let scroll = NSScrollView()
            scroll.hasVerticalScroller = true
            let height = max(290, fields.count * 34 + 10)
            let view = NSView(frame: NSRect(x:0,y:0,width:475,height:height))
            for (i, row) in fields.enumerated() {
                guard let key = row["key"] as? String, let title = row["label"] as? String else { continue }
                let value = row["value"] as? String ?? ""
                let y = height - 36 - 34*i
                let label = NSTextField(labelWithString: title)
                label.frame = NSRect(x:10,y:y+3,width:182,height:22)
                view.addSubview(label)
                if let options = row["choices"] as? [[String]] {
                    let control = NSPopUpButton(frame:NSRect(x:191,y:y,width:278,height:26))
                    control.addItems(withTitles: options.map { $0[1] })
                    control.selectItem(at:options.firstIndex { $0[0] == value } ?? 0)
                    control.setAccessibilityLabel(title)
                    view.addSubview(control)
                    captures[key] = { options[control.indexOfSelectedItem][0] }
                } else {
                    let mode = row["picker"] as? String
                    let field = NSTextField(string:value)
                    field.frame = NSRect(x:191,y:y,width:mode == nil ? 270 : 213,height:24)
                    field.setAccessibilityLabel(title)
                    view.addSubview(field)
                    captures[key] = { field.stringValue }
                    if let mode = mode {
                        let picker = Picker(field, mode)
                        let button = NSButton(title:"选择…",target:picker,action:#selector(Picker.pick))
                        button.frame = NSRect(x:406,y:y-1,width:64,height:26)
                        view.addSubview(button)
                        pickers.append(picker)
                    }
                }
            }
            scroll.documentView = view
            view.scroll(NSPoint(x:0,y:height))
            item.view = scroll
            tabs.addTabViewItem(item)
        }
        if !groups.isEmpty { alert.accessoryView = tabs }
        let buttons = form["buttons"] as? [String] ?? ["确定"]
        for title in buttons {
            let button = alert.addButton(withTitle:title)
            if title == "取消" || title == "关闭" { button.keyEquivalent = "\u{1b}" }
        }
        app.activate(ignoringOtherApps:true)
        let result = withExtendedLifetime(pickers) { alert.runModal() }
        let idx = result.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
        let response: Row = ["action":buttons.indices.contains(idx) ? buttons[idx] : "取消",
                             "values":captures.mapValues { $0() }]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: response))
    }
}
