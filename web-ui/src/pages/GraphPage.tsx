/**
 * Background: Visualization aggregation page with three tabs: knowledge graph force-directed layout,
 * timeline, and embedding scatter plot.
 * Design intent: Tab content is mounted on demand (not hidden)—switching away unmounts,
 * because d3 simulation and large SVGs have high memory overhead; each tab component lives in its own file for maintainability.
 * Key constraint: Tab labels go through t(); no cross-tab state sharing.
 */
import { useState } from "react";
import { Layout } from "../components/Layout";
import { Tabs } from "../components/Tabs";
import { useI18n } from "../i18n";
import { ForceGraphTab } from "./graph/ForceGraphTab";
import { TimelineTab } from "./graph/TimelineTab";
import { EmbeddingTab } from "./graph/EmbeddingTab";

export function GraphPage() {
  const { t } = useI18n();
  const [tab, setTab] = useState("graph");

  return (
    <Layout title={t("Graph")}>
      <Tabs
        items={[
          { key: "graph", label: t("Knowledge graph") },
          { key: "timeline", label: t("Timeline") },
          { key: "embedding", label: t("Embedding map") },
        ]}
        active={tab}
        onChange={setTab}
      />
      <div style={{ paddingTop: 22 }}>
        {tab === "graph" && <ForceGraphTab />}
        {tab === "timeline" && <TimelineTab />}
        {tab === "embedding" && <EmbeddingTab />}
      </div>
    </Layout>
  );
}
