package com.sasu91.dosapp.ui.navigation

import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.DeleteForever
import androidx.compose.material.icons.filled.Inbox
import androidx.compose.material.icons.filled.Inventory
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Today
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.*
import androidx.navigation.compose.*
import com.sasu91.dosapp.ui.connectivity.ConnectivityViewModel
import com.sasu91.dosapp.ui.addarticle.AddArticleScreen
import com.sasu91.dosapp.ui.dispatch.OrderDispatchScreen
import com.sasu91.dosapp.ui.eod.EodScreen
import com.sasu91.dosapp.ui.exceptions.ExceptionScreen
import com.sasu91.dosapp.ui.expiry.ExpiryScreen
import com.sasu91.dosapp.ui.queue.OfflineQueueScreen
import com.sasu91.dosapp.ui.queue.OfflineQueueViewModel
import com.sasu91.dosapp.ui.quickwaste.QuickWasteScreen
import com.sasu91.dosapp.ui.receiving.ReceivingScreen
import com.sasu91.dosapp.ui.scan.ScanScreen
import com.sasu91.dosapp.ui.skubind.SkuEanBindScreen

/** Navigation route definitions */
sealed class Screen(val route: String, val label: String, val icon: ImageVector) {
    object Scan      : Screen("scan",           "Scansione",  Icons.Default.CameraAlt)
    /** Add a new article to the assortment (offline-first). */
    object AddArticle : Screen("add_article",    "Aggiungi articolo", Icons.Default.Add)
    object Exceptions: Screen("exceptions?sku={sku}", "Eccezioni", Icons.Default.Warning) {
        fun withSku(sku: String) = "exceptions?sku=$sku"
    }
    object Receiving : Screen("receiving",      "Ricezione",  Icons.Default.Inventory)
    object Queue      : Screen("offline_queue",  "Coda offline",  Icons.Default.Inbox)
    object QuickWaste : Screen("quick_waste",    "Quick Waste",   Icons.Default.DeleteForever)
    object Eod        : Screen("eod?sku={sku}",  "Chiusura EOD",  Icons.Default.Today) {
        fun withSku(sku: String) = "eod?sku=$sku"
    }
    /** Associate secondary EAN barcodes to SKUs. */
    object SkuBind    : Screen("sku_bind",        "Abbina EAN",    Icons.Default.Link)
    /** View order proposals sent from the desktop to Android terminals. */
    object OrderDispatches : Screen("order_dispatches", "Ordini inviati", Icons.Default.Send)
    /** Local expiry-date tracking — Scadenze feature, no backend sync. */
    object Expiry     : Screen("expiry",           "Scadenze",      Icons.Default.DateRange)
}

private val TOP_LEVEL_SCREENS = listOf(Screen.Scan, Screen.QuickWaste, Screen.Exceptions, Screen.Receiving, Screen.Eod, Screen.Queue, Screen.SkuBind, Screen.AddArticle, Screen.OrderDispatches, Screen.Expiry)

/**
 * Root navigation graph with a Material 3 bottom navigation bar.
 *
 * Bottom bar items: Scan | Eccezioni | Ricezione | Coda offline.
 * The "Coda offline" badge shows pending item count (from Room via OfflineQueueViewModel).
 */
@Composable
fun DosNavGraph(
    queueViewModel: OfflineQueueViewModel = hiltViewModel(),
    connectivityViewModel: ConnectivityViewModel = hiltViewModel(),
) {
    val navController = rememberNavController()
    val currentBackStack by navController.currentBackStackEntryAsState()
    val currentRoute = currentBackStack?.destination?.route
    val pendingCount by queueViewModel.pendingCount.collectAsStateWithLifecycle()
    val connStatus by connectivityViewModel.status.collectAsStateWithLifecycle()

    // ── Auto-retry queued items on Offline→Online transition (foreground only) ─
    // Detects the edge Offline→Online (possibly via Checking) and calls
    // retryAll() once. The guard in retryAll() prevents concurrent invocations.
    LaunchedEffect(Unit) {
        var prevWasOffline = false
        connectivityViewModel.status.collect { status ->
            when (status) {
                ConnectivityViewModel.ConnStatus.Offline -> prevWasOffline = true
                ConnectivityViewModel.ConnStatus.Online  -> {
                    if (prevWasOffline) {
                        prevWasOffline = false
                        queueViewModel.retryAll()
                    }
                }
                else -> Unit  // Checking / Unconfigured — keep prevWasOffline as-is
            }
        }
    }

    // Controls visibility of the feature-launcher popup menu
    var menuExpanded by remember { mutableStateOf(false) }

    Scaffold(
        topBar = { ConnStatusBar(connStatus) },
        floatingActionButton = {
            // Small button anchored bottom-right that opens the feature list
            Box {
                SmallFloatingActionButton(
                    onClick = { menuExpanded = true },
                    containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                    contentColor = MaterialTheme.colorScheme.onSurface,
                ) {
                    Icon(Icons.Default.Menu, contentDescription = "Seleziona funzionalità")
                }
                DropdownMenu(
                    expanded = menuExpanded,
                    onDismissRequest = { menuExpanded = false },
                ) {
                    TOP_LEVEL_SCREENS.forEach { screen ->
                        val selected = currentRoute?.startsWith(screen.route.substringBefore("?")) == true
                        DropdownMenuItem(
                            text = {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                                ) {
                                    if (screen is Screen.Queue && pendingCount > 0) {
                                        BadgedBox(badge = { Badge { Text("$pendingCount") } }) {
                                            Icon(screen.icon, contentDescription = null)
                                        }
                                    } else {
                                        Icon(screen.icon, contentDescription = null)
                                    }
                                    Text(
                                        text = screen.label,
                                        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                    )
                                }
                            },
                            onClick = {
                                menuExpanded = false
                                navController.navigate(
                                    when (screen) {
                                        is Screen.Exceptions -> screen.route.replace("{sku}", "")
                                        is Screen.Eod        -> screen.route.replace("{sku}", "")
                                        else                 -> screen.route
                                    }
                                ) {
                                    popUpTo(navController.graph.startDestinationId) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                        )
                    }
                }
            }
        },
    ) { innerPadding ->
        NavHost(
            navController    = navController,
            startDestination = Screen.Scan.route,
            modifier         = Modifier.padding(innerPadding),
            enterTransition  = { fadeIn() },
            exitTransition   = { fadeOut() },
        ) {
            // Scan screen
            composable(Screen.Scan.route) {
                ScanScreen()
            }

            // Quick Waste — continuous high-speed barcode scanning for waste registration
            composable(Screen.QuickWaste.route) {
                QuickWasteScreen()
            }

            // Exception screen (optional sku arg from Scan)
            composable(
                route     = Screen.Exceptions.route,
                arguments = listOf(navArgument("sku") {
                    nullable = true
                    defaultValue = null
                    type = NavType.StringType
                }),
            ) {
                ExceptionScreen(
                    onNavigateToQueue = { navController.navigate(Screen.Queue.route) },
                )
            }

            // Receiving screen
            composable(Screen.Receiving.route) {
                ReceivingScreen(
                    onNavigateToQueue = { navController.navigate(Screen.Queue.route) },
                )
            }

            // EOD daily-closure screen (optional sku arg from ScanScreen)
            composable(
                route     = Screen.Eod.route,
                arguments = listOf(navArgument("sku") {
                    nullable     = true
                    defaultValue = null
                    type         = NavType.StringType
                }),
            ) {
                EodScreen(
                    onNavigateToQueue = { navController.navigate(Screen.Queue.route) },
                )
            }

            // Offline queue screen
            composable(Screen.Queue.route) {
                OfflineQueueScreen()
            }

            // SKU ↔ secondary EAN binding
            composable(Screen.SkuBind.route) {
                SkuEanBindScreen()
            }

            // Add new article offline-first (queue + local cache)
            composable(Screen.AddArticle.route) {
                AddArticleScreen()
            }

            // Order dispatches — view orders sent from desktop to Android
            composable(Screen.OrderDispatches.route) {
                OrderDispatchScreen()
            }

            // Scadenze — local expiry-date tracking (no backend sync)
            composable(Screen.Expiry.route) {
                ExpiryScreen()
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Connection status bar
// ---------------------------------------------------------------------------

/**
 * Thin top bar showing live backend connectivity status.
 * Displayed at the top of every screen via the Scaffold topBar slot.
 */
@Composable
private fun ConnStatusBar(status: ConnectivityViewModel.ConnStatus) {
    val (dot, label, tint) = when (status) {
        ConnectivityViewModel.ConnStatus.Online        -> Triple("●", "online",           Color(0xFF2E7D32))
        ConnectivityViewModel.ConnStatus.Offline       -> Triple("●", "offline",          Color(0xFFB71C1C))
        ConnectivityViewModel.ConnStatus.Checking      -> Triple("○", "verifica in corso…", Color(0xFF757575))
        ConnectivityViewModel.ConnStatus.Unconfigured  -> Triple("○", "non configurato",  Color(0xFF9E9E9E))
    }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .statusBarsPadding()
            .padding(end = 12.dp, top = 4.dp, bottom = 4.dp),
        horizontalArrangement = Arrangement.End,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Surface(
            shape = MaterialTheme.shapes.extraSmall,
            color = tint.copy(alpha = 0.13f),
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(dot,   color = tint, fontSize = 10.sp)
                Text(label, color = tint, fontSize = 11.sp, fontWeight = FontWeight.Medium)
            }
        }
    }
}
