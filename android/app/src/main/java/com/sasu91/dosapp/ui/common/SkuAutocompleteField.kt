package com.sasu91.dosapp.ui.common

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sasu91.dosapp.data.api.dto.SkuSearchResultDto

/**
 * Reusable SKU autocomplete text field backed by [ExposedDropdownMenuBox].
 *
 * Each suggestion shows:
 *  - Bold SKU code (line 1)
 *  - Description  ·  EAN  /  EAN2  (line 2, muted)
 *
 * Usage:
 * 1. Bind [query] + [onQueryChange] from ViewModel state.
 * 2. When [suggestions] is non-empty AND [expanded] is true the dropdown opens.
 * 3. [onSelect] is called when the user taps a suggestion.
 * 4. [onDismiss] is called on outside-tap or after selection.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SkuAutocompleteField(
    query: String,
    onQueryChange: (String) -> Unit,
    suggestions: List<SkuSearchResultDto>,
    expanded: Boolean,
    onDismiss: () -> Unit,
    onSelect: (SkuSearchResultDto) -> Unit,
    isSearching: Boolean = false,
    label: String = "SKU",
    isError: Boolean = false,
    supportingText: @Composable (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    ExposedDropdownMenuBox(
        expanded         = expanded && suggestions.isNotEmpty(),
        onExpandedChange = { if (!it) onDismiss() },
        modifier         = modifier,
    ) {
        OutlinedTextField(
            value         = query,
            onValueChange = onQueryChange,
            label         = { Text(label) },
            isError       = isError,
            supportingText = supportingText,
            leadingIcon   = {
                if (isSearching) {
                    CircularProgressIndicator(
                        modifier    = Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                    )
                } else {
                    Icon(Icons.Default.Search, contentDescription = null)
                }
            },
            modifier   = Modifier
                .fillMaxWidth()
                .menuAnchor(),
            singleLine = true,
        )

        if (suggestions.isNotEmpty()) {
            ExposedDropdownMenu(
                expanded         = expanded,
                onDismissRequest = onDismiss,
            ) {
                suggestions.forEach { item ->
                    DropdownMenuItem(
                        text = {
                            Column {
                                Text(
                                    text       = item.sku,
                                    fontWeight = FontWeight.SemiBold,
                                    fontSize   = 13.sp,
                                )
                                val detail = buildString {
                                    append(item.description)
                                    if (!item.ean.isNullOrBlank())
                                        append("  ·  ${item.ean}")
                                    if (!item.eanSecondary.isNullOrBlank())
                                        append(" / ${item.eanSecondary}")
                                }
                                Text(
                                    text  = detail,
                                    fontSize = 11.sp,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    maxLines = 1,
                                )
                            }
                        },
                        onClick = {
                            onSelect(item)
                            onDismiss()
                        },
                    )
                }
            }
        }
    }
}
