// Test if all modules can load and have required exports
import { state, api, login, logout, fetchCurrentUser, can } from "./core.js";
console.log("core.js exports:", Object.keys({state,api,login,logout,fetchCurrentUser,can}).join(","));

import { renderDashboard } from "./dashboard.js";
import { renderHotspots } from "./hotspots.js";
import { renderArticles, renderArticle } from "./articles.js";
import { renderConfig } from "./config-page.js";
import { renderBilling } from "./billing.js";
import { renderPaymentOrders } from "./payment-orders.js";
import { renderHelp, renderHistory, renderTasks } from "./operations.js";
import { renderLogin } from "./login.js";
import { renderUsers } from "./users.js";
import { renderAudit } from "./audit.js";
import { renderTokenUsage } from "./token-usage.js";

console.log("All imports OK");
