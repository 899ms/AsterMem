/**
 * Background: Home dashboard—the first page users see after logging in.
 * Design intent: Clean data cards showing system status (total memories, tag count, vectorization progress),
 * with feature module descriptions below to guide users through getting started.
 * Key constraint: Stats from /api/stats; feature descriptions go through i18n; avoid information overload.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IconArrowRight } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { useI18n } from "../i18n";
import { api } from "../api";

interface Stats {
  total?: number;
  active?: number;
  tags?: string[];
  vector_count?: number;
  trunk_total?: number;
  trunk_ready?: number;
}

export function HomePage() {
  const { t } = useI18n();
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    api<Stats>("GET", "/api/stats")
      .then((res) => setStats(res ?? {}))
      .catch(() => setStats({}));
  }, []);

  const features = [
    {
      title: t("Memories"),
      desc: t("Open the memory library, keep writing, or pull content back with semantic search."),
      to: "/memories",
    },
    {
      title: t("Explore"),
      desc: t("Ask the memory library directly. AI brings the related context into its answer."),
      to: "/explore",
    },
    {
      title: t("Graph"),
      desc: t("View your memories in a knowledge graph, timeline, or vector space."),
      to: "/graph",
    },
    {
      title: t("Import / Export"),
      desc: t("Import text, files, and bookmarks directly. Export a backup when you need one."),
      to: "/import",
    },
    {
      title: t("Tags"),
      desc: t("Organize memories with a tag tree and filter them faster."),
      to: "/tags",
    },
    {
      title: t("Settings"),
      desc: t("Configure embedding and chat providers, then test the connection."),
      to: "/settings",
    },
  ];

  return (
    <Layout title={t("Home")}>
      <section className="home-hero">
        <h2 className="home-headline">{t("What do you want to find today?")}</h2>
        <p className="home-sub">{t("Your memories are here. Keep writing, search the library, ask AI, or open the graph.")}</p>
      </section>

      <Link to="/new" className="home-ai-banner">
        <span className="kicker">ASTERMEM SKILL / AI FIRST</span>
        <div>
          <h2>{t("Connect your AI first")}</h2>
          <p>{t("Download the AsterMem Skill, send the setup instructions to your AI, and let it handle memories, providers, and system settings through the API.")}</p>
        </div>
        <span className="home-ai-banner-link">
          {t("Set up AI")}
          <IconArrowRight aria-hidden="true" />
        </span>
      </Link>

      {stats && (
        <section className="home-stats">
          <div className="stat-card">
            <span className="stat-value">{stats.active ?? stats.total ?? "—"}</span>
            <span className="stat-label">{t("Memories")}</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{stats.tags?.length ?? "—"}</span>
            <span className="stat-label">{t("Tags")}</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">
              {stats.vector_count != null ? stats.vector_count : "—"}
            </span>
            <span className="stat-label">{t("Vectorized")}</span>
          </div>
        </section>
      )}

      <section className="home-features">
        {features.map((f) => (
          <Link key={f.to} to={f.to} className="feature-card">
            <h3>{f.title}</h3>
            <p>{f.desc}</p>
          </Link>
        ))}
      </section>
    </Layout>
  );
}
