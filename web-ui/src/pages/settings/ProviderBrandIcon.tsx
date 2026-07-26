const LOCAL_ICONS: Record<string, string> = {
  tokendance: "/icons/tokendance.jpg",
  asterove: "/icons/asterove.jpg",
};

const PROVIDER_ICON_KEYS: Record<string, string> = {
  anthropic: "anthropic",
  openai: "openai",
  xai: "xai",
  google: "google",
  moonshot: "moonshot",
  kimi_coding: "moonshot",
  dashscope: "bailian",
  aliyun_coding: "alibabacloud",
  xiaomi: "xiaomimimo",
  xiaomi_coding: "xiaomimimo",
  deepseek: "deepseek",
  zhipu: "zhipu",
  zhipu_coding: "zhipu",
  minimax: "minimax",
  minimax_coding: "minimax",
  volces: "volcengine",
  volces_coding: "volcengine",
  openrouter: "openrouter",
  pipellm_claude: "claude",
  siliconflow: "siliconcloud",
  lmstudio: "lmstudio",
  ollama: "ollama",
};

export function ProviderBrandIcon({ id, size = 30 }: { id: string; size?: number }) {
  const localSrc = LOCAL_ICONS[id];
  const iconId = PROVIDER_ICON_KEYS[id];
  const src = localSrc ?? (iconId ? `/icons/providers/${iconId}.png` : null);

  return (
    <span className="provider-brand-icon" aria-hidden="true">
      {src ? (
        <img
          src={src}
          width={size}
          height={size}
          alt=""
          loading="lazy"
          style={localSrc ? { borderRadius: 6 } : undefined}
        />
      ) : (
        <i style={{ width: size, height: size }}>{id.slice(0, 1).toUpperCase()}</i>
      )}
    </span>
  );
}
