/**
 * Background: The landing page listed features as static cards; nothing showed *how* AsterMem
 * actually treats a piece of data, and that flow is the product's strongest pitch.
 * Design intent: A scroll-pinned assembly line, read left to right. One real memory enters from
 * the agent apps, becomes a card, lands on disk, is parsed into chunks and vectors, comes back
 * on recall, and ends up sealed in a vault. Each act adds a station and a spotlight slides onto
 * it; earlier stations stay on stage, dimmed, wired together by marching-ants links carrying a
 * travelling packet, so the whole data path is visible at once.
 * Key constraint: No animation library — a rAF-throttled scroll handler writes `--p` (global
 * progress) as a CSS variable and flips a `stage-N` class; all motion is CSS, so
 * `prefers-reduced-motion` can disable it wholesale in CSS.
 */
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../../i18n";
import grokIcon from "@lobehub/icons-static-svg/icons/grok.svg";
import claudeCodeIcon from "@lobehub/icons-static-svg/icons/claudecode-color.svg";
import codexIcon from "@lobehub/icons-static-svg/icons/codex.svg";
import cursorIcon from "@lobehub/icons-static-svg/icons/cursor.svg";
import geminiIcon from "@lobehub/icons-static-svg/icons/gemini-color.svg";
import copilotIcon from "@lobehub/icons-static-svg/icons/copilot-color.svg";
import hermesIcon from "@lobehub/icons-static-svg/icons/hermesagent.svg";
import openClawIcon from "@lobehub/icons-static-svg/icons/openclaw-color.svg";

const STAGE_COUNT = 5;
const STAGE_LABELS = ["CAPTURE", "STORE", "PARSE", "RECALL", "PRIVATE"];

/* Eight, so the grid fills two clean rows and the caption reads as a real roster. */
const APPS = [
  { name: "Grok", icon: grokIcon },
  { name: "Claude Code", icon: claudeCodeIcon },
  { name: "Codex", icon: codexIcon },
  { name: "Cursor", icon: cursorIcon },
  { name: "Gemini CLI", icon: geminiIcon },
  { name: "Copilot", icon: copilotIcon },
  { name: "Hermes", icon: hermesIcon },
  { name: "OpenClaw", icon: openClawIcon },
];

export function LandingStory() {
  const { t } = useI18n();
  const rootRef = useRef<HTMLElement>(null);
  const [stage, setStage] = useState(0);
  const stageRef = useRef(0);

  useEffect(() => {
    let raf = 0;
    const update = () => {
      raf = 0;
      const el = rootRef.current;
      if (!el) return;
      const total = el.offsetHeight - window.innerHeight;
      if (total <= 0) return;
      const p = Math.min(1, Math.max(0, -el.getBoundingClientRect().top / total));
      el.style.setProperty("--p", p.toFixed(4));
      const next = Math.min(STAGE_COUNT - 1, Math.floor(p * STAGE_COUNT));
      if (next !== stageRef.current) {
        stageRef.current = next;
        setStage(next);
      }
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  // Stations light up on their own act and stay on stage afterwards as dimmed context.
  const nodeClass = (act: number) =>
    `story-node act${act} ${stage === act ? "is-live" : stage > act ? "is-past" : ""}`;
  const wireClass = (act: number) =>
    `story-wire ${stage === act ? "is-live" : stage > act ? "is-past" : ""}`;

  const stages = [
    { title: t("It walks in on its own"), desc: t("Tell your AI once. Claude Code, Codex, Cursor — whichever agent you use drops what it just learned into AsterMem with a single API call.") },
    { title: t("It lands on your disk"), desc: t("A single folder on your machine: Markdown you can read, SQLite you can query, a vector index that rebuilds itself. Backing up means copying data/ — nothing more.") },
    { title: t("It gets taken apart"), desc: t("Long content is carved into semantic chunks, tagged, and embedded as vectors, so every paragraph can be found on its own.") },
    { title: t("It comes back when you need it"), desc: t("Keyword and vector search run side by side. Ask in different words and it still comes back — the exact paragraph, not a paraphrase.") },
    { title: t("It never leaves home"), desc: t("No cloud, no telemetry, nobody else's server. Sign-in protection, scoped API tokens, and AGPL-3.0 source you can audit.") },
  ];

  return (
    <section ref={rootRef} className={`landing-story stage-${stage}`} aria-label={t("The life of one memory")}>
      <div className="landing-story-sticky">
        <div className="story-drift" aria-hidden="true">
          <span>+</span><span>+</span><span>+</span><span>+</span><span>+</span><span>+</span>
        </div>

        <header className="story-head">
          <span className="kicker">ASTERMEM / 0{stage + 1} {STAGE_LABELS[stage]}</span>
          <h2>{t("The life of one memory")}</h2>
          <span className="story-hint">{t("Scroll to follow it")}</span>
          <div className="story-progress" aria-hidden="true"><i /></div>
        </header>

        <div className="story-main">
          <div className="story-copy-col">
            {stages.map((s, i) => (
              <div key={s.title} className={`story-copy${i === stage ? " active" : ""}`}>
                <em>0{i + 1}</em>
                <h3>{s.title}</h3>
                <p>{s.desc}</p>
              </div>
            ))}
          </div>

          <div className="story-visual" aria-hidden="true">
            {/* Faint background hints so the dark stage never reads as empty */}
            <div className="story-ghost-num">
              <span>01</span><span>02</span><span>03</span><span>04</span><span>05</span>
            </div>

            {/* Spotlight that slides onto whichever station is on stage */}
            <div className="story-beam" />

            {/* The assembly line, built up station by station and never torn down */}
            <div className="story-pipe">
              <div className={nodeClass(0)}>
                <b className="story-step">01 CAPTURE</b>
                <div className="story-bay">
                  <div className="story-apps">
                    {APPS.map((a) => (
                      <span className="story-app" key={a.name}>
                        <img src={a.icon} alt="" loading="lazy" />
                      </span>
                    ))}
                  </div>
                  <div className="story-apps-caption">{APPS.map((a) => a.name).join(" · ")}</div>
                  <div className="story-snippet">“{t("…and stop writing 'it's not X, it's Y' at me. It reads like a machine…")}”</div>
                </div>
              </div>

              <div className={wireClass(0)}><i /></div>

              <div className="story-node story-hero is-live">
                <b className="story-step lit">THE MEMORY</b>
                <div className="story-bay">
                  <div className="story-card">
                    <span>MEMORY / 024</span>
                    <strong>{t("Never write 'it's not X, it's Y'")}</strong>
                    <div className="story-card-tags">
                      <em># {t("WRITING/STYLE")}</em>
                      <em># {t("HOW I LIKE THINGS")}</em>
                    </div>
                  </div>
                </div>
              </div>

              <div className={wireClass(1)}><i /></div>

              <div className={nodeClass(1)}>
                <b className="story-step">02 STORE</b>
                <div className="story-bay">
                  <div className="story-disk">
                    <span className="story-disk-tab">data/</span>
                    <div className="story-files">
                      <span>2026-07/mem_0424.md</span>
                      <span>index.sqlite</span>
                      <span>chroma/ · whoosh/</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className={wireClass(2)}><i /></div>

              <div className={nodeClass(2)}>
                <b className="story-step">03 PARSE</b>
                <div className="story-bay">
                  <div className="story-chunks">
                    <div className="story-chunk"><i>¶ 01</i>{t("chunk · {n} tokens", { n: 84 })}</div>
                    <div className="story-chunk"><i>¶ 02</i>{t("chunk · {n} tokens", { n: 66 })}</div>
                    <div className="story-chunk"><i>¶ 03</i>{t("chunk · {n} tokens", { n: 102 })}</div>
                  </div>
                  <div className="story-vector">
                    <span className="ghost">[ 0.88 0.14 −0.61 … ]</span>
                    <span>[ 0.12 −0.48 0.87 … ]</span>
                  </div>
                </div>
              </div>

              <div className={wireClass(3)}><i /></div>

              <div className={nodeClass(3)}>
                <b className="story-step">04 RECALL</b>
                <div className="story-bay">
                  <div className="story-recall-head">
                    <span className="story-query">? {t("what did I say about how I want you to write")}</span>
                    <span className="story-score">HYBRID 0.94</span>
                  </div>
                  <div className="story-answer">
                    <span>{t("RECALLED")} · ¶ 02</span>
                    <p>“{t("…drop the 'not X, but Y' construction — just say the thing…")}”</p>
                  </div>
                </div>
              </div>

              {/* Act 5 seals everything built so far, rather than replacing it */}
              <div className={`story-vault${stage === 4 ? " is-live" : ""}`}>
                <em className="tl">05 PRIVATE</em>
                <em className="tr">NO CLOUD · NO TELEMETRY</em>
                <em className="bl">YOUR DISK ONLY</em>
                <em className="br">AGPL-3.0</em>
              </div>
            </div>
          </div>
        </div>

        <footer className="story-rail" aria-hidden="true">
          {STAGE_LABELS.map((label, i) => (
            <span key={label} className={i === stage ? "active" : ""}><i />{label}</span>
          ))}
        </footer>
      </div>
    </section>
  );
}
