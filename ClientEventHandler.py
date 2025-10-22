#!/usr/bin/env python3
#
#  Copyright (C) Hudiy Project - All Rights Reserved
#

class ClientEventHandler:
    """Base class for handling events from the Hudiy API."""
    
    def on_hello_response(self, client, message):
        """Called when hello response is received."""
        pass
    
    def on_media_status(self, client, message):
        """Called when media status is updated."""
        pass
    
    def on_media_metadata(self, client, message):
        """Called when media metadata is updated."""
        pass
    
    def on_navigation_status(self, client, message):
        """Called when navigation status is updated."""
        pass
    
    def on_navigation_maneuver_details(self, client, message):
        """Called when navigation maneuver details are updated."""
        pass
    
    def on_navigation_maneuver_distance(self, client, message):
        """Called when navigation maneuver distance is updated."""
        pass
    
    def on_phone_connection_status(self, client, message):
        """Called when phone connection status is updated."""
        pass
    
    def on_phone_levels_status(self, client, message):
        """Called when phone levels status is updated."""
        pass
    
    def on_phone_voice_call_status(self, client, message):
        """Called when phone voice call status is updated."""
        pass
