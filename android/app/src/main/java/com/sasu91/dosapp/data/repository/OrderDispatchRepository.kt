package com.sasu91.dosapp.data.repository

import com.sasu91.dosapp.data.api.DosApiService
import com.sasu91.dosapp.data.api.dto.OrderDispatchDeleteResponseDto
import com.sasu91.dosapp.data.api.dto.OrderDispatchResponseDto
import com.sasu91.dosapp.data.api.dto.OrderDispatchSummaryDto
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Repository for the Order Dispatch endpoints.
 *
 * - GET  /api/v1/order-dispatches          → [fetchDispatches]
 * - GET  /api/v1/order-dispatches/{id}     → [fetchDispatchDetail]
 * - DELETE /api/v1/order-dispatches/{id}   → [deleteDispatch]
 * - DELETE /api/v1/order-dispatches        → [deleteAllDispatches]
 *
 * No offline queue: dispatches are read-only from the Android side.
 * The desktop writes them; Android only reads and optionally deletes.
 */
@Singleton
class OrderDispatchRepository @Inject constructor(
    private val api: DosApiService,
) {

    /** Fetch the most recent dispatches (server returns max 10). */
    suspend fun fetchDispatches(): ApiResult<List<OrderDispatchSummaryDto>> =
        safeCall { api.listOrderDispatches().toApiResult() }

    /** Fetch a single dispatch with all embedded lines. */
    suspend fun fetchDispatchDetail(dispatchId: String): ApiResult<OrderDispatchResponseDto> =
        safeCall { api.getOrderDispatch(dispatchId).toApiResult() }

    /** Delete a single dispatch. */
    suspend fun deleteDispatch(dispatchId: String): ApiResult<OrderDispatchDeleteResponseDto> =
        safeCall { api.deleteOrderDispatch(dispatchId).toApiResult() }

    /** Delete all dispatches. */
    suspend fun deleteAllDispatches(): ApiResult<OrderDispatchDeleteResponseDto> =
        safeCall { api.deleteAllOrderDispatches().toApiResult() }
}
