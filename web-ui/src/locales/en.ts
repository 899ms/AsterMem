// en patch dictionary: English is the source language; most keys are already English text and need no entry.
// However, backend profile field labels/hints are sent in Chinese (keys are Chinese), and the English UI
// also needs translations for them, so this file only contains those non-English keys.
// Other keys fall back to the original text via t(). This file is intentionally a partial dictionary
// and excluded from check-i18n key completeness validation.
const dict: Record<string, string> = {
  "称呼": "Preferred name",
  "性别": "Gender",
  "常用语言": "Languages",
  "时区": "Time zone",
  "职业": "Occupation",
  "所在地": "Location",
  "组织 / 团队": "Organization / team",
  "当前重点": "Current focus",
  "沟通偏好": "Communication preferences",
  "禁忌 / 避免": "Things to avoid",
  "希望 AI 怎么称呼你": "What the AI should call you",
  "如 中文 / English": "e.g. English / 中文",
  "如 Asia/Shanghai": "e.g. Asia/Shanghai",
  "正在推进的项目或领域": "Projects or areas you are working on",
  "如 回答要简洁、代码注释用中文": "e.g. keep answers concise",
  "不希望 AI 做的事": "Things you do not want the AI to do",
};

export default dict;
