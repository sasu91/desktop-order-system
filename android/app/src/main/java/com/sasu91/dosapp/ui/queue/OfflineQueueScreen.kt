package com.sasu91.dosapp.ui.queue

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sasu91.dosapp.data.db.entity.PendingRequestEntity
import com.sasu91.dosapp.data.db.entity.RequestStatus

/**
 * Offline queue screen.
 *
 * Lists all PENDING / FAILED requests stored locally.
 * Each row has a [Retry] button that re-posts the saved payload.
 * A toolbar button purges already-SENT rows.
 */
@Composable
fun OfflineQueueScreen(
    viewModel: OfflineQueueViewModel = hiltViewModel(),
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Coda offline (${state.items.size})") },
                actions = {
                    TextButton(onClick = viewModel::deleteSent) {
                        Text("Pulisci inviati")
                    }
                },
            )
        },
    ) { padding ->
        if (state.items.isEmpty()) {
            Box(
                Modifier
                    .padding(padding)
                    .fillMaxSize(),
                contentAlignment = Alignment.Center,
            ) {
                Text("Nessuna richiesta in coda.", style = MaterialTheme.typography.bodyLarge)
            }
        } else {
            LazyColumn(
                Modifier
                    .padding(padding)
                    .fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(state.items, key = { it.id }) { entity ->
                    QueueItemCard(
                        entity  = entity,
                        isBusy  = entity.id in state.busyIds,
                        onRetry = { viewModel.retry(entity) },
                    )
                }
            }
        }
    }
}

@Composable
private fun QueueItemCard(
    entity: PendingRequestEntity,
    isBusy: Boolean,
    onRetry: () -> Unit,
) {
    val containerColor = when (entity.status) {
        RequestStatus.FAILED  -> MaterialTheme.colorScheme.errorContainer
        RequestStatus.SENT    -> MaterialTheme.colorScheme.surfaceVariant
        else                  -> MaterialTheme.colorScheme.surface
    }

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors   = CardDefaults.cardColors(containerColor = containerColor),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Row(
            Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {

                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    // Type chip
                    SuggestionChip(
                        onClick  = {},
                        label    = { Text(entity.type.name, style = MaterialTheme.typography.labelSmall) },
                    )
                    // Status chip
                    SuggestionChip(
                        onClick  = {},
                        label    = { Text(entity.status.name, style = MaterialTheme.typography.labelSmall) },
                    )
                }

                // Human-readable summary
                Text(
                    entity.summary,
                    style   = MaterialTheme.typography.bodyMedium,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )

                if (entity.retryCount > 0) {
                    Text(
                        "Tentativi: ${entity.retryCount}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.outline,
                    )
                }

                if (entity.lastError != null) {
                    Text(
                        entity.lastError,
                        style   = MaterialTheme.typography.labelSmall,
                        color   = MaterialTheme.colorScheme.error,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }

            Spacer(Modifier.width(8.dp))

            // Retry button (hidden for SENT items)
            if (entity.status != RequestStatus.SENT) {
                if (isBusy) {
                    CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp)
                } else {
                    IconButton(onClick = onRetry) {
                        Icon(Icons.Default.Refresh, contentDescription = "Riprova")
                    }
                }
            }
        }
    }
}
