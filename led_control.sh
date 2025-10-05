#!/bin/bash
# Simple bash script to control WS2812B LEDs via curl
# Works on any Linux system with curl installed
#
# Usage examples:
#   ./led_control.sh red
#   ./led_control.sh 255 128 0
#   ./led_control.sh off
#   ./led_control.sh health

# Configuration
LED_CONTROLLER="http://localhost:8080"

# Color presets
declare -A COLORS=(
    ["red"]="255 0 0"
    ["green"]="0 255 0"
    ["blue"]="0 0 255"
    ["white"]="255 255 255"
    ["yellow"]="255 255 0"
    ["cyan"]="0 255 255"
    ["magenta"]="255 0 255"
    ["orange"]="255 165 0"
    ["purple"]="128 0 128"
    ["off"]="0 0 0"
)

# Function to set LED color
set_color() {
    local r=$1
    local g=$2
    local b=$3
    
    echo "Setting LEDs to RGB($r, $g, $b)..."
    response=$(curl -s -w "\n%{http_code}" "${LED_CONTROLLER}/update?r=${r}&g=${g}&b=${b}")
    http_code=$(echo "$response" | tail -n1)
    
    if [ "$http_code" -eq 200 ]; then
        echo "✓ Success"
    else
        echo "✗ Error: HTTP $http_code"
    fi
}

# Function to get health status
get_health() {
    echo "Fetching health status..."
    curl -s "${LED_CONTROLLER}/health" | python3 -m json.tool
}

# Main script
if [ $# -eq 0 ]; then
    echo "WS2812B LED Controller - Command Line Tool"
    echo "=========================================="
    echo ""
    echo "Usage:"
    echo "  $0 <preset>           Set color by name"
    echo "  $0 <r> <g> <b>        Set color by RGB values (0-255)"
    echo "  $0 health             Show health status"
    echo ""
    echo "Available presets:"
    for color in "${!COLORS[@]}"; do
        echo "  - $color"
    done | sort
    echo ""
    echo "Examples:"
    echo "  $0 red"
    echo "  $0 255 128 0"
    echo "  $0 off"
    echo "  $0 health"
    exit 0
fi

# Handle commands
case "$1" in
    health)
        get_health
        ;;
    *)
        if [ $# -eq 1 ]; then
            # Try to match preset color
            preset_name="$1"
            if [ -n "${COLORS[$preset_name]}" ]; then
                read -r r g b <<< "${COLORS[$preset_name]}"
                set_color "$r" "$g" "$b"
            else
                echo "Error: Unknown preset '$preset_name'"
                echo "Run '$0' without arguments to see available presets"
                exit 1
            fi
        elif [ $# -eq 3 ]; then
            # RGB values provided
            set_color "$1" "$2" "$3"
        else
            echo "Error: Invalid number of arguments"
            echo "Run '$0' without arguments for usage information"
            exit 1
        fi
        ;;
esac
