<template>
<AmbientBackdrop />

<div class="app">
  <OrbitalBar />

  <!-- ================= WORKSPACE ================= -->
  <div class="workspace" id="workspace">
    <StreamPane />
    <RightDock />
  </div>

  <StatusBar />
</div>

<!-- ================= PREVIEW STAGE ================= -->
<PreviewStage />

<!-- ================= COMMAND PALETTE ================= -->
<CommandPalette />

<!-- ================= LEDGER SHEET ================= -->


<LedgerPanel />

<NewEntryDialog />

<!-- ================= MCP SHEET ================= -->

<McpPanel />

<!-- ================= PAIRING SHEET ================= -->
<PairingPanel />

<!-- ================= PRICING SHEET ================= -->


<!-- ================= PROJECT SHEET ================= -->


<!-- ================= APPEARANCE SHEET ================= -->
<ThemeSheet />

<NotifySheet />

<!-- ================= COMPACT CONFIRM ================= -->
<CompactConfirm />

<!-- ================= PATH WHITELIST ================= -->
<PathsPanel />

<!-- ================= TOOLS ================= -->
<ToolsPanel />

<!-- ================= PERMISSION DIALOG ================= -->


<!-- ================= PERMISSION GATE ================= -->
<PermissionGate />

<!-- ================= IMAGE VIEWER ================= -->
<ImageViewer />

<!-- ================= DROP VEIL ================= -->
<DropOverlay />

<!-- ================= CONVERSATION MENU ================= -->
<ConversationMenu />

<!-- ================= MESSAGE SEARCH ================= -->
<MessageSearch />

<!-- ================= CONFIRM SHEET ================= -->
<ConfirmSheet />

<!-- TOASTS -->
<ToastStack />
</template>

<script setup lang="ts">
import AmbientBackdrop from './components/AmbientBackdrop.vue'
import OrbitalBar from './components/OrbitalBar.vue'
import StreamPane from './components/StreamPane.vue'
import RightDock from './components/RightDock.vue'
import StatusBar from './components/StatusBar.vue'
import PreviewStage from './components/PreviewStage.vue'
import CommandPalette from './components/CommandPalette.vue'
import LedgerPanel from './components/LedgerPanel.vue'
import NewEntryDialog from './components/NewEntryDialog.vue'
import McpPanel from './components/McpPanel.vue'
import PairingPanel from './components/PairingPanel.vue'
import ThemeSheet from './components/ThemeSheet.vue'
import NotifySheet from './components/NotifySheet.vue'
import CompactConfirm from './components/CompactConfirm.vue'
import PathsPanel from './components/PathsPanel.vue'
import ToolsPanel from './components/ToolsPanel.vue'
import PermissionGate from './components/PermissionGate.vue'
import ImageViewer from './components/ImageViewer.vue'
import DropOverlay from './components/DropOverlay.vue'
import ConversationMenu from './components/ConversationMenu.vue'
import MessageSearch from './components/MessageSearch.vue'
import ConfirmSheet from './components/ConfirmSheet.vue'
import ToastStack from './components/ToastStack.vue'
</script>
