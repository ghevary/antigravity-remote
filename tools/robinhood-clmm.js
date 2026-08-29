#!/usr/bin/env node
/**
 * Meridian Robinhood Chain CLMM Paper Trading Engine (Uniswap v3/v4)
 * Strictly isolated from Solana / Meteora DLMM paper-trading records.
 */

import fs from "fs";
import path from "path";
import os from "os";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Strictly isolated data paths for Robinhood Chain
const DATA_DIR = path.join(os.homedir(), ".hermes", "profiles", "ghepappo", "home", ".meridian");
const POSITIONS_FILE = path.join(DATA_DIR, "robinhood_paper_positions.json");
const POLICY_FILE = path.join(DATA_DIR, "robinhood_strategy_policy.json");
const STATE_FILE = path.join(__dirname, "..", "robinhood-state.json");

if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

function loadPositions() {
  if (fs.existsSync(POSITIONS_FILE)) {
    try {
      return JSON.parse(fs.readFileSync(POSITIONS_FILE, "utf8"));
    } catch {
      return { positions: [], closed_positions: [], cumulative_pnl_usd: 0.0 };
    }
  }
  return { positions: [], closed_positions: [], cumulative_pnl_usd: 0.0 };
}

function savePositions(data) {
  fs.writeFileSync(POSITIONS_FILE, JSON.stringify(data, null, 2), "utf8");
}

async function fetchRobinhoodPairs() {
  const url = "https://api.dexscreener.com/latest/dex/search?q=robinhood";
  try {
    const res = await fetch(url, { headers: { "User-Agent": "Meridian-Robinhood/1.0" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    return (json.pairs || []).filter(p => p.chainId === "robinhood");
  } catch (e) {
    console.error("Error fetching DEXScreener data:", e.message);
    return [];
  }
}

export async function scanRobinhoodPools() {
  console.log("🔍 Scanning Uniswap v3/v4 Pools on Robinhood Chain (Chain ID: 4663)...");
  const pairs = await fetchRobinhoodPairs();
  if (pairs.length === 0) {
    console.log("No active pairs returned from DEXScreener API.");
    return [];
  }

  const scored = pairs.map(p => {
    const base = p.baseToken?.symbol || "UNKNOWN";
    const quote = p.quoteToken?.symbol || "UNKNOWN";
    const priceUsd = Number(p.priceUsd) || 0;
    const vol24h = Number(p.volume?.h24) || 0;
    const liqUsd = Number(p.liquidity?.usd) || 0;
    const labels = p.labels || [];
    const version = labels.includes("v4") ? "v4" : "v3";
    const feeTier = 0.003;
    const est24hFees = vol24h * feeTier;
    const feeLiqRatio = liqUsd > 0 ? (est24hFees / liqUsd) * 100 : 0;

    return {
      pairAddress: p.pairAddress,
      dexId: p.dexId,
      version,
      symbol: `${base}/${quote}`,
      baseToken: p.baseToken,
      quoteToken: p.quoteToken,
      priceUsd,
      vol24h,
      liqUsd,
      est24hFees,
      feeLiqRatio,
      url: p.url
    };
  });

  scored.sort((a, b) => b.vol24h - a.vol24h);

  console.log("\n📊 Top Robinhood Chain Pool Candidates:");
  console.log("--------------------------------------------------------------------------------");
  console.log(`| # | Pool / DEX       | Price (USD)   | 24h Vol ($)   | Liquidity ($) | Est. Fee/Day |`);
  console.log("--------------------------------------------------------------------------------");
  scored.slice(0, 10).forEach((p, i) => {
    const idx = String(i + 1).padEnd(2);
    const name = `${p.symbol} (${p.version})`.padEnd(16);
    const pr = `$${p.priceUsd.toFixed(6)}`.padEnd(13);
    const vol = `$${p.vol24h.toLocaleString()}`.padEnd(13);
    const liq = `$${p.liqUsd.toLocaleString()}`.padEnd(13);
    const fee = `$${p.est24hFees.toFixed(2)}`.padEnd(12);
    console.log(`| ${idx} | ${name} | ${pr} | ${vol} | ${liq} | ${fee} |`);
  });
  console.log("--------------------------------------------------------------------------------\n");
  return scored;
}

export async function deployRobinhoodPaperPosition({ pairAddress, amountUsd = 60.0, rangePct = 15.0 }) {
  const data = loadPositions();
  const pairs = await fetchRobinhoodPairs();
  const targetPair = pairs.find(p => p.pairAddress?.toLowerCase() === pairAddress.toLowerCase()) || pairs[0];

  if (!targetPair) {
    console.error("Pool pair not found on Robinhood Chain.");
    return false;
  }

  const base = targetPair.baseToken?.symbol || "BASE";
  const quote = targetPair.quoteToken?.symbol || "QUOTE";
  const currentPrice = Number(targetPair.priceUsd) || 0.0001;
  const lowerPrice = currentPrice * (1 - rangePct / 100);
  const upperPrice = currentPrice * (1 + rangePct / 100);
  const posId = `rh_${Date.now().toString(36)}`;

  const newPosition = {
    id: posId,
    network: "robinhood",
    chainId: 4663,
    pairAddress: targetPair.pairAddress,
    dex: targetPair.dexId || "uniswap",
    symbol: `${base}/${quote}`,
    baseSymbol: base,
    quoteSymbol: quote,
    capitalUsd: amountUsd,
    entryPriceUsd: currentPrice,
    lowerPriceUsd: lowerPrice,
    upperPriceUsd: upperPrice,
    rangePct,
    openedAt: new Date().toISOString(),
    status: "OPEN_PAPER",
    accumulatedFeeUsd: 0.0,
    currentPnLUsd: 0.0,
    lastCheckTime: Date.now()
  };

  data.positions.push(newPosition);
  savePositions(data);

  console.log(`✅ [Robinhood Chain] Paper Position Deployed:`);
  console.log(`   ID: ${posId}`);
  console.log(`   Pool: ${base}/${quote} (${targetPair.pairAddress})`);
  console.log(`   Capital: $${amountUsd}`);
  console.log(`   Entry Price: $${currentPrice.toFixed(6)}`);
  console.log(`   Range: [$${lowerPrice.toFixed(6)} - $${upperPrice.toFixed(6)}] (+/-${rangePct}%)`);
  return newPosition;
}

export async function monitorRobinhoodPositions() {
  console.log("👀 Monitoring Robinhood Chain Paper Positions...");
  const data = loadPositions();
  const pairs = await fetchRobinhoodPairs();
  const pairMap = new Map(pairs.map(p => [p.pairAddress?.toLowerCase(), p]));

  if (data.positions.length === 0) {
    console.log("No active Robinhood paper positions open.");
    return [];
  }

  const now = Date.now();
  let anyClosed = false;

  for (const pos of data.positions) {
    const livePair = pairMap.get(pos.pairAddress?.toLowerCase());
    const currentPrice = livePair ? Number(livePair.priceUsd) || pos.entryPriceUsd : pos.entryPriceUsd;
    const vol24h = livePair ? Number(livePair.volume?.h24) || 0 : 0;
    const liqUsd = livePair ? Number(livePair.liquidity?.usd) || 10000 : 10000;

    const inRange = currentPrice >= pos.lowerPriceUsd && currentPrice <= pos.upperPriceUsd;
    const hoursElapsed = Math.max(0.1, (now - (pos.lastCheckTime || now)) / 3600000);
    
    // Fee estimation: pro-rata pool volume * 0.3% fee * (position_capital / pool_liquidity)
    const poolShare = Math.min(1.0, pos.capitalUsd / (liqUsd || 10000));
    const hourlyPoolFee = (vol24h * 0.003) / 24;
    const feeEarned = inRange ? (hourlyPoolFee * poolShare * hoursElapsed) : 0.0;
    
    pos.accumulatedFeeUsd += feeEarned;
    pos.lastCheckTime = now;

    // Mark-to-market PnL
    const priceChangePct = ((currentPrice - pos.entryPriceUsd) / pos.entryPriceUsd) * 100;
    const ilPct = inRange ? (priceChangePct * 0.5) : (currentPrice < pos.lowerPriceUsd ? -15.0 : 15.0);
    pos.currentPnLUsd = ((pos.capitalUsd * ilPct) / 100) + pos.accumulatedFeeUsd;

    console.log(`- [${pos.id}] ${pos.symbol}: Current Price $${currentPrice.toFixed(6)} | Status: ${inRange ? "IN RANGE" : "OUT OF RANGE"} | Fees: +$${pos.accumulatedFeeUsd.toFixed(2)} | Net PnL: $${pos.currentPnLUsd.toFixed(2)}`);

    // Exit rules: Take-profit +5% or Stop-loss -50%
    if (pos.currentPnLUsd >= (pos.capitalUsd * 0.05)) {
      console.log(`🎯 Take Profit reached (+${pos.currentPnLUsd.toFixed(2)} USD). Closing position.`);
      pos.status = "CLOSED_PAPER";
      pos.closedAt = new Date().toISOString();
      pos.exitReason = "TAKE_PROFIT";
      data.closed_positions = data.closed_positions || [];
      data.closed_positions.push(pos);
      data.cumulative_pnl_usd = (data.cumulative_pnl_usd || 0.0) + pos.currentPnLUsd;
      anyClosed = true;
    } else if (pos.currentPnLUsd <= -(pos.capitalUsd * 0.50)) {
      console.log(`🛑 Stop Loss hit (-${Math.abs(pos.currentPnLUsd).toFixed(2)} USD). Closing position.`);
      pos.status = "CLOSED_PAPER";
      pos.closedAt = new Date().toISOString();
      pos.exitReason = "STOP_LOSS";
      data.closed_positions = data.closed_positions || [];
      data.closed_positions.push(pos);
      data.cumulative_pnl_usd = (data.cumulative_pnl_usd || 0.0) + pos.currentPnLUsd;
      anyClosed = true;
    }
  }

  if (anyClosed) {
    data.positions = data.positions.filter(p => p.status === "OPEN_PAPER");
  }

  savePositions(data);
  return data.positions;
}

export function printRobinhoodSummary() {
  const data = loadPositions();
  const openCount = (data.positions || []).length;
  const closedCount = (data.closed_positions || []).length;
  const realizedPnL = Number(data.cumulative_pnl_usd) || 0.0;
  const unrealizedPnL = (data.positions || []).reduce((acc, p) => acc + (Number(p.currentPnLUsd) || 0), 0);
  const baseEquity = 60.0;
  const currentEquity = baseEquity + realizedPnL + unrealizedPnL;

  console.log("\n============================================================");
  console.log(" Robinhood Chain (EVM L2) Paper-Trading Analytics");
  console.log("    (Isolated Dataset - Separated from Meteora DLMM)");
  console.log("============================================================");
  console.log(`[+] Network: Robinhood Chain (Arbitrum Orbit / Chain ID: 4663)`);
  console.log(`[+] Primary DEX: Uniswap v3 & v4 (Concentrated Liquidity)`);
  console.log(`[+] Open Positions: ${openCount}`);
  console.log(`[+] Closed Positions: ${closedCount}`);
  console.log(`[+] Realized PnL: ${realizedPnL >= 0 ? "+" : ""}$${realizedPnL.toFixed(2)} USD`);
  console.log(`[+] Unrealized PnL: ${unrealizedPnL >= 0 ? "+" : ""}$${unrealizedPnL.toFixed(2)} USD`);
  console.log(`[+] Total Robinhood Paper Equity: $${currentEquity.toFixed(2)} USD`);
  console.log("============================================================\n");
  return { openCount, closedCount, realizedPnL, unrealizedPnL, currentEquity };
}

async function main() {
  const cmd = process.argv[2] || "scan";

  if (cmd === "scan") {
    await scanRobinhoodPools();
  } else if (cmd === "deploy") {
    const pairArg = process.argv[3] || "";
    await deployRobinhoodPaperPosition({ pairAddress: pairArg });
  } else if (cmd === "monitor") {
    await monitorRobinhoodPositions();
  } else if (cmd === "summary" || cmd === "status") {
    printRobinhoodSummary();
  } else {
    console.log("Usage: node robinhood-clmm.js [scan|deploy <pair>|monitor|summary]");
  }
}

if (process.argv[1] && process.argv[1].endsWith("robinhood-clmm.js")) {
  main().catch(console.error);
}
