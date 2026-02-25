package com.sasu91.dosapp

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.sasu91.dosapp.ui.navigation.DosNavGraph
import com.sasu91.dosapp.ui.theme.DosAppTheme
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            DosAppTheme {
                DosNavGraph()
            }
        }
    }
}
