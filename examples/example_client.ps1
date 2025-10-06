# WS2812B LED Controller - PowerShell Client
# Uses native PowerShell cmdlets (Invoke-WebRequest) - no dependencies!
#
# Usage examples:
#   .\example_client.ps1 -Color red
#   .\example_client.ps1 -R 255 -G 128 -B 0
#   .\example_client.ps1 -Off
#   .\example_client.ps1 -Health
#   .\example_client.ps1 -Rainbow
#   .\example_client.ps1 -Breathe

param(
    [string]$Color,
    [int]$R = -1,
    [int]$G = -1,
    [int]$B = -1,
    [switch]$Off,
    [switch]$Health,
    [switch]$Rainbow,
    [switch]$Breathe,
    [string]$BreatheColor = "white",
    [string]$Url = "http://localhost:8080"
)

# Color presets
$ColorPresets = @{
    "red"     = @(255, 0, 0)
    "green"   = @(0, 255, 0)
    "blue"    = @(0, 0, 255)
    "white"   = @(255, 255, 255)
    "yellow"  = @(255, 255, 0)
    "cyan"    = @(0, 255, 255)
    "magenta" = @(255, 0, 255)
    "orange"  = @(255, 165, 0)
    "purple"  = @(128, 0, 128)
    "pink"    = @(255, 192, 203)
}

function Set-LEDColor {
    param(
        [int]$Red,
        [int]$Green,
        [int]$Blue
    )
    
    try {
        $uri = "$Url/update?r=$Red&g=$Green&b=$Blue"
        $response = Invoke-WebRequest -Uri $uri -Method Get -TimeoutSec 5 -UseBasicParsing
        
        if ($response.StatusCode -eq 200) {
            Write-Host "✓ Set LEDs to RGB($Red, $Green, $Blue)" -ForegroundColor Green
            return $true
        } else {
            Write-Host "✗ Error: HTTP $($response.StatusCode)" -ForegroundColor Red
            return $false
        }
    }
    catch {
        Write-Host "✗ Connection error: $_" -ForegroundColor Red
        return $false
    }
}

function Get-LEDHealth {
    try {
        $response = Invoke-WebRequest -Uri "$Url/health" -Method Get -TimeoutSec 5 -UseBasicParsing
        
        if ($response.StatusCode -eq 200) {
            $health = $response.Content | ConvertFrom-Json
            
            Write-Host "`n==================================================" -ForegroundColor Cyan
            Write-Host "LED Controller Health Status" -ForegroundColor Cyan
            Write-Host "==================================================" -ForegroundColor Cyan
            Write-Host "Status:           $($health.status)" -ForegroundColor Green
            Write-Host "Server Uptime:    $($health.server_uptime)"
            Write-Host "Updates:          $($health.updates_processed)"
            Write-Host "LEDs:             $($health.num_leds)"
            Write-Host "Current Color:    R=$($health.current_color.r) G=$($health.current_color.g) B=$($health.current_color.b)"
            Write-Host "`nSystem Info:" -ForegroundColor Yellow
            Write-Host "Platform:         $($health.system.platform) $($health.system.platform_release)"
            Write-Host "Python:           $($health.system.python_version)"
            Write-Host "CPU Count:        $($health.system.cpu_count)"
            
            if ($health.system.memory) {
                Write-Host "Memory Used:      $($health.system.memory.used_percent)%"
                Write-Host "Memory Available: $($health.system.memory.available_mb) MB"
            }
            
            if ($health.system.load_average) {
                Write-Host "Load Average:     $($health.system.load_average -join ', ')"
            }
            
            if ($health.system.cpu_temp_c) {
                Write-Host "CPU Temperature:  $($health.system.cpu_temp_c)°C"
            }
            
            if ($health.system.system_uptime) {
                Write-Host "System Uptime:    $($health.system.system_uptime)"
            }
            
            Write-Host "==================================================" -ForegroundColor Cyan
        } else {
            Write-Host "✗ Error: HTTP $($response.StatusCode)" -ForegroundColor Red
        }
    }
    catch {
        Write-Host "✗ Connection error: $_" -ForegroundColor Red
        Write-Host "Make sure the LED controller is running at $Url" -ForegroundColor Yellow
    }
}

function Start-RainbowCycle {
    Write-Host "`n🌈 Rainbow cycle (Press Ctrl+C to stop)..." -ForegroundColor Magenta
    
    $colors = @(
        @(255, 0, 0),      # Red
        @(255, 127, 0),    # Orange
        @(255, 255, 0),    # Yellow
        @(0, 255, 0),      # Green
        @(0, 0, 255),      # Blue
        @(75, 0, 130),     # Indigo
        @(148, 0, 211)     # Violet
    )
    
    try {
        while ($true) {
            foreach ($color in $colors) {
                Set-LEDColor -Red $color[0] -Green $color[1] -Blue $color[2] | Out-Null
                Start-Sleep -Milliseconds 100
            }
        }
    }
    catch {
        Write-Host "`n⏹ Stopped" -ForegroundColor Yellow
    }
}

function Start-BreathingEffect {
    param(
        [int[]]$Color = @(255, 255, 255)
    )
    
    Write-Host "`n💨 Breathing effect with RGB($($Color -join ', ')) (Press Ctrl+C to stop)..." -ForegroundColor Cyan
    
    $steps = 20
    $delay = 50
    
    try {
        while ($true) {
            # Fade in
            for ($i = 0; $i -le $steps; $i++) {
                $brightness = $i / $steps
                $r = [int]($Color[0] * $brightness)
                $g = [int]($Color[1] * $brightness)
                $b = [int]($Color[2] * $brightness)
                Set-LEDColor -Red $r -Green $g -Blue $b | Out-Null
                Start-Sleep -Milliseconds $delay
            }
            
            # Fade out
            for ($i = $steps; $i -ge 0; $i--) {
                $brightness = $i / $steps
                $r = [int]($Color[0] * $brightness)
                $g = [int]($Color[1] * $brightness)
                $b = [int]($Color[2] * $brightness)
                Set-LEDColor -Red $r -Green $g -Blue $b | Out-Null
                Start-Sleep -Milliseconds $delay
            }
        }
    }
    catch {
        Write-Host "`n⏹ Stopped" -ForegroundColor Yellow
    }
}

function Show-Usage {
    Write-Host "`nWS2812B LED Controller - PowerShell Client" -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "`nUsage:" -ForegroundColor Yellow
    Write-Host "  Set color by name:"
    Write-Host "    .\example_client.ps1 -Color <preset>" -ForegroundColor Gray
    Write-Host "`n  Set color by RGB values:"
    Write-Host "    .\example_client.ps1 -R <0-255> -G <0-255> -B <0-255>" -ForegroundColor Gray
    Write-Host "`n  Special commands:"
    Write-Host "    .\example_client.ps1 -Off              # Turn LEDs off" -ForegroundColor Gray
    Write-Host "    .\example_client.ps1 -Health           # Show health status" -ForegroundColor Gray
    Write-Host "    .\example_client.ps1 -Rainbow          # Rainbow cycle animation" -ForegroundColor Gray
    Write-Host "    .\example_client.ps1 -Breathe          # Breathing effect (white)" -ForegroundColor Gray
    Write-Host "    .\example_client.ps1 -Breathe -BreatheColor red" -ForegroundColor Gray
    Write-Host "`n  Custom URL:"
    Write-Host "    .\example_client.ps1 -Color red -Url http://192.168.1.100:8080" -ForegroundColor Gray
    Write-Host "`nAvailable color presets:" -ForegroundColor Yellow
    foreach ($preset in $ColorPresets.Keys | Sort-Object) {
        Write-Host "  - $preset"
    }
    Write-Host "`nExamples:" -ForegroundColor Yellow
    Write-Host "  .\example_client.ps1 -Color red"
    Write-Host "  .\example_client.ps1 -R 255 -G 128 -B 0"
    Write-Host "  .\example_client.ps1 -Rainbow"
    Write-Host "  .\example_client.ps1 -Health"
}

# Main logic
if ($Health) {
    Get-LEDHealth
}
elseif ($Rainbow) {
    Start-RainbowCycle
}
elseif ($Breathe) {
    if ($ColorPresets.ContainsKey($BreatheColor.ToLower())) {
        Start-BreathingEffect -Color $ColorPresets[$BreatheColor.ToLower()]
    } else {
        Write-Host "Unknown color preset: $BreatheColor" -ForegroundColor Red
        Write-Host "Available presets: $($ColorPresets.Keys -join ', ')"
    }
}
elseif ($Off) {
    Set-LEDColor -Red 0 -Green 0 -Blue 0
}
elseif ($Color) {
    $colorLower = $Color.ToLower()
    if ($ColorPresets.ContainsKey($colorLower)) {
        $rgb = $ColorPresets[$colorLower]
        Set-LEDColor -Red $rgb[0] -Green $rgb[1] -Blue $rgb[2]
    } else {
        Write-Host "Unknown color preset: $Color" -ForegroundColor Red
        Write-Host "Available presets: $($ColorPresets.Keys -join ', ')"
    }
}
elseif ($R -ge 0 -and $G -ge 0 -and $B -ge 0) {
    Set-LEDColor -Red $R -Green $G -Blue $B
}
else {
    Show-Usage
}
