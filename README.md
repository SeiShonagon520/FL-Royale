<p align="center"><img src="assets/branding/firstlight-icon.png" width="160" alt="FL Royale"></p>

# FL Royale

**Fully offline Clash Royale AI battles on Android.** Challenge the competitive general-purpose AI or test yourself against a Hall of Fame–level 2.6 Hog expert, powered by [Firstlight CR](https://github.com/Jaasssoooonnnnn/FirstLight_CR).

[简体中文](README.zh-CN.md) · [Build guide](docs/build.md)

- **Completely offline.** The game engine, resources and all five AI models are bundled in the APK. Battles and AI inference run entirely on your device, with no server connection, account, computer or root required to play.
- **Competitive general-purpose AI.** The built-in Firstlight model provides a strong opponent across different decks, with two additional general models offering different playstyles.

## In-game screenshots

<table>
  <tr>
    <th width="25%">Offline lobby</th>
    <th width="25%">Deck editor</th>
    <th width="25%">Choose your AI</th>
    <th width="25%">Battle against AI</th>
  </tr>
  <tr>
    <td><a href="assets/screenshots/lobby.png"><img src="assets/screenshots/lobby.png" width="100%" alt="FL Royale offline lobby"></a></td>
    <td><a href="assets/screenshots/deck-editor.png"><img src="assets/screenshots/deck-editor.png" width="100%" alt="Native deck editor with the USE FOR AI button"></a></td>
    <td><a href="assets/screenshots/ai-selection.png"><img src="assets/screenshots/ai-selection.png" width="100%" alt="Selection of five built-in AI opponents"></a></td>
    <td><a href="assets/screenshots/hog-expert-battle.png"><img src="assets/screenshots/hog-expert-battle.png" width="100%" alt="Live battle against 2.6 master 1"></a></td>
  </tr>
</table>

This repository contains the source and build tools for **1.8-r1**. It depends on Firstlight CR for the initial offline APK, model checkpoints and shared observation/action code. Game binaries and downloaded game resources are supplied locally.

## Playing

1. Edit your eight-card deck in **Collection → Decks**.
2. Tap **USE FOR AI** on the Decks page to save the current deck for the AI. You can then change your own deck independently.
3. Tap **Battle** and choose an AI opponent.
4. Play using the native hand and arena controls.
5. Return to the lobby after the match. Decks, nickname and cosmetic selections persist locally.

The app includes 122 supported cards, 42 evolution forms, 24 hero forms and four tower troops. Base card/tower levels are 11. Online shop, clan, ranking and event services are unavailable.

The target is **Android 7+ / ARM64**. The package is `com.firstlight.offline` and can coexist with Firstlight CR's desktop-controlled engine.

## Building

Follow the [build guide](docs/build.md). In outline:

1. Follow Firstlight CR's setup guide and build `cr-ai-offline-cache.apk`.
2. Copy the supported game's downloaded update files to a local folder.
3. Set your paths in `build.local.json` and run `tools/build_offline_app.ps1`.

The build exports the five checkpoints to ONNX, compiles the native bridge and Android runtime, bundles the game resources, and signs the final APK with a local key.

## Source layout

| Directory | Contents |
| --- | --- |
| `baseline/native_runner/probe` | Native battle integration, offline Home, decks and cosmetics, human input timing |
| `on_device` | Java AI/session UI and Python observation/action adapter |
| `android_runtime_smoke/localapp` | Gradle project used to compile the embedded Android runtime |
| `resource_bootstrap` | First-launch installation of bundled game resources |
| `research/mobile_inference` | ONNX export wrapper and tensor ABI used by the build |
| `research/snapshot_bootstrap_review` | Native local-battle bootstrap used by the build |
| `tools` | Build, resource packaging and APK patching |

Project source is under [Apache-2.0](LICENSE). See [NOTICE](NOTICE) for the Firstlight CR attribution and scope of the license.
