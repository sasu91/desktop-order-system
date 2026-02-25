package com.sasu91.dosapp.ui.navigation

import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Inbox
import androidx.compose.material.icons.filled.Inventory
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.*
import androidx.navigation.compose.*
import com.sasu91.dosapp.ui.exceptions.ExceptionScreen
import com.sasu91.dosapp.ui.queue.OfflineQueueScreen
import com.sasu91.dosapp.ui.queue.OfflineQueueViewModel
import com.sasu91.dosapp.ui.receiving.ReceivingScreen
import com.sasu91.dosapp.ui.scan.ScanScreen

/** Navigation route definitions */
sealed class Screen(val route: String, val label: String, val icon: ImageVector) {
    object Scan      : Screen("scan",           "Scansione",  Icons.Default.CameraAlt)
    object Exceptions: Screen("exceptions?sku={sku}", "Eccezioni", Icons.Default.Warning) {
        fun withSku(sku: String) = "exceptions?sku=$sku"
    }
    object Receiving : Screen("receiving",      "Ricezione",  Icons.Default.Inventory)
    object Queue     : Screen("offline_queue",  "Coda offline", Icons.Default.Inbox)
}

private val TOP_LEVEL_SCREENS = listOf(Screen.Scan, Screen.Exceptions, Screen.Receiving, Screen.Queue)

/**
 * Root navigation graph with a Material 3 bottom navigation bar.
 *
 * Bottom bar items: Scan | Eccezioni | Ricezione | Coda offline.
 * The "Coda offline" badge shows pending item count (from Room via OfflineQueueViewModel).
 */
@Composable
fun DosNavGraph(
    queueViewModel: OfflineQueueViewModel = hiltViewModel(),
) {
    val navController = rememberNavController()
    val currentBackStack by navController.currentBackStackEntryAsState()
    val currentRoute = currentBackStack?.destination?.route
    val pendingCount by queueViewModel.pendingCount.collectAsStateWithLifecycle()

    Scaffold(
        bottomBar = {
            NavigationBar {
                TOP_LEVEL_SCREENS.forEach { screen ->
                    val selected = currentRoute?.startsWith(screen.route.substringBefore("?")) == true

                    NavigationBarItem(
                        selected = selected,
                        onClick  = {
                            navController.navigate(
                                if (screen is Screen.Exceptions) screen.route.replace("{sku}", "") else screen.route
                            ) {
                                popUpTo(navController.graph.startDestinationId) { saveState = true }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = {
                            if (screen is Screen.Queue && pendingCount > 0) {
                                BadgedBox(badge = { Badge { Text("$pendingCount") } }) {
                                    Icon(screen.icon, contentDescription = screen.label)
                                }
                            } else {
                                Icon(screen.icon, contentDescription = screen.label)
                            }
                        },
                        label = { Text(screen.label) },
                    )
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
                ScanScreen(
                    onNavigateToExceptions = { sku ->
                        navController.navigate(Screen.Exceptions.withSku(sku))
                    },
                )
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

            // Offline queue screen
            composable(Screen.Queue.route) {
                OfflineQueueScreen()
            }
        }
    }
}
