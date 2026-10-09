<script lang="ts">
  import { t } from "./lib/strings";
  import Status from "./Status.svelte";

  // The screen the app opens on once it is set up. Status only for now; Settings and Jobs come later.
  let { home, onsetup }: { home: string; onsetup: () => void } = $props();
</script>

<h1 tabindex="-1">{t("home.title")}</h1>
<Status {home} {onsetup} />
