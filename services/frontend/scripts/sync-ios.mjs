import { readFileSync, writeFileSync, copyFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { brand, xmlText } from './native-brand.mjs'

export function configureInfo (plist) {
  if (!plist.includes('<dict>')) throw new Error('Missing iOS Info.plist dictionary')
  for (const [key, value] of Object.entries({
    CFBundleDisplayName: brand.name,
    NSMicrophoneUsageDescription: `Record dictation for your ${brand.name} instance.`
  })) {
    const entry = `<key>${key}</key>\n\t<string>${xmlText(value)}</string>`
    const pattern = new RegExp(`<key>${key}</key>\\s*<string>[\\s\\S]*?</string>`)
    plist = pattern.test(plist) ? plist.replace(pattern, () => entry) : plist.replace('<dict>', `<dict>\n\t${entry}`)
  }
  return plist
}

export function configureProject (project) {
  if (project.includes('PrivacyInfo.xcprivacy in Resources')) return project
  const replacements = [
    ['/* End PBXBuildFile section */', '\t\tAA0000000000000000000001 /* PrivacyInfo.xcprivacy in Resources */ = {isa = PBXBuildFile; fileRef = AA0000000000000000000002 /* PrivacyInfo.xcprivacy */; };\n'],
    ['/* End PBXFileReference section */', '\t\tAA0000000000000000000002 /* PrivacyInfo.xcprivacy */ = {isa = PBXFileReference; lastKnownFileType = text.xml; path = PrivacyInfo.xcprivacy; sourceTree = "<group>"; };\n'],
    ['\t\t\t\t504EC3131FED79650016851F /* Info.plist */,', '\t\t\t\tAA0000000000000000000002 /* PrivacyInfo.xcprivacy */,\n'],
    ['\t\t\t\t504EC30F1FED79650016851F /* Assets.xcassets in Resources */,', '\t\t\t\tAA0000000000000000000001 /* PrivacyInfo.xcprivacy in Resources */,\n']
  ]
  for (const [marker, addition] of replacements) {
    if (!project.includes(marker)) throw new Error(`iOS template changed: missing ${marker}`)
    project = project.replace(marker, addition + marker)
  }
  return project
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const info = 'ios/App/App/Info.plist'
  const project = 'ios/App/App.xcodeproj/project.pbxproj'
  writeFileSync(info, configureInfo(readFileSync(info, 'utf8')))
  writeFileSync(project, configureProject(readFileSync(project, 'utf8')))
  copyFileSync('native/ios/PrivacyInfo.xcprivacy', 'ios/App/App/PrivacyInfo.xcprivacy')
  copyFileSync('native/icons/app-icon.png', 'ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png')
}
