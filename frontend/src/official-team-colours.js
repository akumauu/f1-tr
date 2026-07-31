// 车手与车队身份色的唯一来源。
// 数值逐赛季取自 Formula 1 官方成绩页内的 `teamColourCode`，不得用近似色、
// 哈希色、车号取模色或跨赛季回填替代。

const OFFICIAL_SOURCE_BY_SEASON = Object.freeze({
  2023: Object.freeze({
    provider: "Formula 1",
    field: "teamColourCode",
    url: "https://www.formula1.com/en/results/2023/team",
  }),
  2024: Object.freeze({
    provider: "Formula 1",
    field: "teamColourCode",
    url: "https://www.formula1.com/en/results/2024/team",
  }),
  2025: Object.freeze({
    provider: "Formula 1",
    field: "teamColourCode",
    url: "https://www.formula1.com/en/results/2025/team",
  }),
  2026: Object.freeze({
    provider: "Formula 1",
    field: "teamColourCode",
    url: "https://www.formula1.com/en/results/2026/team",
  }),
});

function team(key, name, colour, aliases = []) {
  return Object.freeze({
    key,
    name,
    colour,
    aliases: Object.freeze([name, ...aliases]),
  });
}

const OFFICIAL_TEAMS_BY_SEASON = Object.freeze({
  2023: Object.freeze([
    team("alfa-romeo", "Alfa Romeo", "#C92D4B", [
      "Alfa Romeo Ferrari",
      "Alfa Romeo F1 Team Stake",
    ]),
    team("alphatauri", "AlphaTauri", "#5E8FAA", [
      "AlphaTauri Honda RBPT",
      "Scuderia AlphaTauri",
    ]),
    team("alpine", "Alpine", "#2293D1", [
      "Alpine Renault",
      "BWT Alpine F1 Team",
    ]),
    team("aston-martin", "Aston Martin", "#358C75", [
      "Aston Martin Aramco Mercedes",
      "Aston Martin Aramco Cognizant F1 Team",
    ]),
    team("ferrari", "Ferrari", "#F91536", ["Scuderia Ferrari"]),
    team("haas", "Haas F1 Team", "#B6BABD", [
      "Haas",
      "Haas Ferrari",
      "MoneyGram Haas F1 Team",
    ]),
    team("mclaren", "McLaren", "#F58020", [
      "McLaren Mercedes",
      "McLaren F1 Team",
    ]),
    team("mercedes", "Mercedes", "#6CD3BF", [
      "Mercedes-AMG Petronas F1 Team",
    ]),
    team("red-bull-racing", "Red Bull Racing", "#3671C6", [
      "Red Bull",
      "Red Bull Racing Honda RBPT",
      "Oracle Red Bull Racing",
    ]),
    team("williams", "Williams", "#37BEDD", [
      "Williams Mercedes",
      "Williams Racing",
    ]),
  ]),
  2024: Object.freeze([
    team("alpine", "Alpine", "#0093CC", [
      "Alpine Renault",
      "BWT Alpine F1 Team",
    ]),
    team("aston-martin", "Aston Martin", "#229971", [
      "Aston Martin Aramco Mercedes",
      "Aston Martin Aramco F1 Team",
    ]),
    team("ferrari", "Ferrari", "#E80020", ["Scuderia Ferrari"]),
    team("haas", "Haas F1 Team", "#B6BABD", [
      "Haas",
      "Haas Ferrari",
      "MoneyGram Haas F1 Team",
    ]),
    team("kick-sauber", "Kick Sauber", "#52E252", [
      "Kick Sauber Ferrari",
      "Stake F1 Team Kick Sauber",
    ]),
    team("mclaren", "McLaren", "#FF8000", [
      "McLaren Mercedes",
      "McLaren F1 Team",
    ]),
    team("mercedes", "Mercedes", "#27F4D2", [
      "Mercedes-AMG Petronas F1 Team",
    ]),
    team("rb", "RB", "#6692FF", [
      "RB Honda RBPT",
      "Visa Cash App RB F1 Team",
    ]),
    team("red-bull-racing", "Red Bull Racing", "#3671C6", [
      "Red Bull",
      "Red Bull Racing Honda RBPT",
      "Oracle Red Bull Racing",
    ]),
    team("williams", "Williams", "#64C4FF", [
      "Williams Mercedes",
      "Williams Racing",
    ]),
  ]),
  2025: Object.freeze([
    team("alpine", "Alpine", "#00A1E8", [
      "Alpine Renault",
      "BWT Alpine Formula One Team",
    ]),
    team("aston-martin", "Aston Martin", "#229971", [
      "Aston Martin Aramco Mercedes",
      "Aston Martin Aramco F1 Team",
    ]),
    team("ferrari", "Ferrari", "#ED1131", ["Scuderia Ferrari HP"]),
    team("haas", "Haas F1 Team", "#9C9FA2", [
      "Haas",
      "Haas Ferrari",
      "MoneyGram Haas F1 Team",
    ]),
    team("kick-sauber", "Kick Sauber", "#01C00E", [
      "Kick Sauber Ferrari",
      "Stake F1 Team Kick Sauber",
    ]),
    team("mclaren", "McLaren", "#F47600", [
      "McLaren Mercedes",
      "McLaren F1 Team",
    ]),
    team("mercedes", "Mercedes", "#00D7B6", [
      "Mercedes-AMG Petronas F1 Team",
    ]),
    team("racing-bulls", "Racing Bulls", "#6C98FF", [
      "Racing Bulls Honda RBPT",
      "Visa Cash App Racing Bulls F1 Team",
    ]),
    team("red-bull-racing", "Red Bull Racing", "#4781D7", [
      "Red Bull",
      "Red Bull Racing Honda RBPT",
      "Oracle Red Bull Racing",
    ]),
    team("williams", "Williams", "#1868DB", [
      "Williams Mercedes",
      "Williams Racing",
    ]),
  ]),
  2026: Object.freeze([
    team("alpine", "Alpine", "#00A1E8", [
      "Alpine Mercedes",
      "BWT Alpine Formula One Team",
    ]),
    team("aston-martin", "Aston Martin", "#229971", [
      "Aston Martin Aramco Honda",
      "Aston Martin Aramco F1 Team",
    ]),
    team("audi", "Audi", "#FF2D00", ["Audi Revolut F1 Team"]),
    team("cadillac", "Cadillac", "#AAAAAD", [
      "Cadillac Ferrari",
      "Cadillac Formula 1 Team",
    ]),
    team("ferrari", "Ferrari", "#E8002D", ["Scuderia Ferrari HP"]),
    team("haas", "Haas F1 Team", "#DEE1E2", [
      "Haas",
      "Haas Ferrari",
      "TGR Haas F1 Team",
    ]),
    team("mclaren", "McLaren", "#FF8000", [
      "McLaren Mercedes",
      "McLaren F1 Team",
    ]),
    team("mercedes", "Mercedes", "#27F4D2", [
      "Mercedes-AMG Petronas F1 Team",
    ]),
    team("racing-bulls", "Racing Bulls", "#6692FF", [
      "Racing Bulls Red Bull Ford",
      "Visa Cash App Racing Bulls F1 Team",
    ]),
    team("red-bull-racing", "Red Bull Racing", "#3671C6", [
      "Red Bull",
      "Red Bull Racing Red Bull Ford",
      "Oracle Red Bull Racing",
    ]),
    team("williams", "Williams", "#1868DB", [
      "Atlassian Williams Mercedes",
      "Williams Racing",
    ]),
  ]),
});

function normalizeTeamName(value) {
  return String(value || "")
    .normalize("NFKD")
    .toLocaleLowerCase("en")
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

const TEAM_LOOKUP_BY_SEASON = new Map(
  Object.entries(OFFICIAL_TEAMS_BY_SEASON).map(([year, rows]) => {
    const lookup = new Map();
    for (const row of rows) {
      for (const alias of row.aliases) {
        const normalized = normalizeTeamName(alias);
        if (lookup.has(normalized)) {
          throw new Error(`官方车队颜色别名冲突：${year} · ${alias}`);
        }
        lookup.set(normalized, row);
      }
    }
    return [Number(year), lookup];
  }),
);

function resolveOfficialTeamIdentity(year, teamName) {
  const season = Number(year);
  const normalizedName = normalizeTeamName(teamName);
  const source = OFFICIAL_SOURCE_BY_SEASON[season] || null;
  const row = TEAM_LOOKUP_BY_SEASON.get(season)?.get(normalizedName) || null;
  if (!row) {
    return {
      status: "unmapped",
      year: Number.isInteger(season) ? season : null,
      key: null,
      name: String(teamName || "").trim() || null,
      colour: null,
      source,
    };
  }
  return {
    status: "official",
    year: season,
    key: row.key,
    name: row.name,
    colour: row.colour,
    source,
  };
}

function requireOfficialTeamIdentity(year, teamName) {
  const identity = resolveOfficialTeamIdentity(year, teamName);
  if (identity.status !== "official") {
    throw new Error(
      `官方车队颜色未映射：${identity.year ?? "未知赛季"} · ${identity.name || "未知车队"}`,
    );
  }
  return identity;
}

function officialTeamColoursForSeason(year) {
  return (OFFICIAL_TEAMS_BY_SEASON[Number(year)] || []).map((row) => ({
    key: row.key,
    name: row.name,
    colour: row.colour,
  }));
}

export {
  OFFICIAL_SOURCE_BY_SEASON,
  OFFICIAL_TEAMS_BY_SEASON,
  normalizeTeamName,
  officialTeamColoursForSeason,
  requireOfficialTeamIdentity,
  resolveOfficialTeamIdentity,
};
