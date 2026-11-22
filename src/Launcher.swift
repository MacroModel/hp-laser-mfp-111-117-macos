import Foundation
let home=FileManager.default.homeDirectoryForCurrentUser
let proc=Process()
proc.executableURL=home.appendingPathComponent("Library/Application Support/HP115nw Studio/current/run")
proc.arguments=["home"]
do {try proc.run();proc.waitUntilExit();exit(proc.terminationStatus)} catch {fputs("HP 115nw Studio is unavailable\n",stderr);exit(1)}
