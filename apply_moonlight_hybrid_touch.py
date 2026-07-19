#!/usr/bin/env python3
"""Patch Moonlight iOS so Absolute Touch uses two-finger remote scrolling.

Run this script from the root of a moonlight-stream/moonlight-ios checkout.
It intentionally repurposes Touchscreen/Absolute Touch mode:
  * 1 finger: direct absolute touch/click/drag
  * 2 fingers: remote high-resolution vertical/horizontal scrolling
  * 3 fingers: existing keyboard gesture

Local pinch-to-zoom and canvas panning are disabled because they conflict with
remote two-finger scrolling.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path.cwd()
ABSOLUTE_PATH = ROOT / "Limelight/Input/AbsoluteTouchHandler.m"
FRAME_PATH = ROOT / "Limelight/ViewControllers/StreamFrameViewController.m"

NEW_ABSOLUTE = r'''//
//  AbsoluteTouchHandler.m
//  Moonlight
//
//  Created by Cameron Gutman on 11/1/20.
//  Copyright © 2020 Moonlight Game Streaming Project. All rights reserved.
//

#import "AbsoluteTouchHandler.h"

#include <Limelight.h>

// How long the fingers must be stationary to start a right click
#define LONG_PRESS_ACTIVATION_DELAY 0.650f

// How far the finger can move before it cancels a right click
#define LONG_PRESS_ACTIVATION_DELTA 0.01f

// How long the double tap deadzone stays in effect between touch up and touch down
#define DOUBLE_TAP_DEAD_ZONE_DELAY 0.250f

// How far the finger can move before it can override the double tap deadzone
#define DOUBLE_TAP_DEAD_ZONE_DELTA 0.025f

// Briefly defer the left-button press so a second finger can begin scrolling
// without producing an accidental click on the host.
#define DIRECT_TOUCH_ACTIVATION_DELAY 0.120f

// Matches the high-resolution two-finger scrolling used by RelativeTouchHandler.
#define TWO_FINGER_SCROLL_MULTIPLIER 10.0f

@implementation AbsoluteTouchHandler {
    StreamView* view;

    NSTimer* directTouchTimer;
    NSTimer* longPressTimer;
    UITouch* lastTouchDown;
    CGPoint lastTouchDownLocation;
    UITouch* lastTouchUp;
    CGPoint lastTouchUpLocation;

    BOOL leftButtonDown;
    BOOL rightButtonDown;
    BOOL isTwoFingerScrolling;
    CGPoint lastTwoFingerLocation;
}

- (id)initWithView:(StreamView*)view {
    self = [self init];
    self->view = view;
    return self;
}

- (CGPoint)centerOfTouches:(NSSet *)touches {
    CGFloat x = 0.0f;
    CGFloat y = 0.0f;
    NSUInteger count = 0;

    for (UITouch* touch in touches) {
        if (touch.phase != UITouchPhaseEnded && touch.phase != UITouchPhaseCancelled) {
            CGPoint location = [touch locationInView:view];
            x += location.x;
            y += location.y;
            count++;
        }
    }

    if (count == 0) {
        return CGPointZero;
    }

    return CGPointMake(x / count, y / count);
}

- (void)cancelTimers {
    [directTouchTimer invalidate];
    directTouchTimer = nil;

    [longPressTimer invalidate];
    longPressTimer = nil;
}

- (void)releaseButtons {
    if (leftButtonDown) {
        LiSendMouseButtonEvent(BUTTON_ACTION_RELEASE, BUTTON_LEFT);
        leftButtonDown = NO;
    }

    if (rightButtonDown) {
        LiSendMouseButtonEvent(BUTTON_ACTION_RELEASE, BUTTON_RIGHT);
        rightButtonDown = NO;
    }
}

- (void)cancelDirectTouch {
    [self cancelTimers];
    [self releaseButtons];
}

- (void)activateLeftButton {
    [directTouchTimer invalidate];
    directTouchTimer = nil;

    if (!leftButtonDown && !rightButtonDown && !isTwoFingerScrolling) {
        LiSendMouseButtonEvent(BUTTON_ACTION_PRESS, BUTTON_LEFT);
        leftButtonDown = YES;
    }
}

- (void)onDirectTouchStart:(NSTimer*)timer {
    [self activateLeftButton];
}

- (void)onLongPressStart:(NSTimer*)timer {
    [directTouchTimer invalidate];
    directTouchTimer = nil;

    // Raise the left click and start a right click.
    if (leftButtonDown) {
        LiSendMouseButtonEvent(BUTTON_ACTION_RELEASE, BUTTON_LEFT);
        leftButtonDown = NO;
    }

    if (!rightButtonDown) {
        LiSendMouseButtonEvent(BUTTON_ACTION_PRESS, BUTTON_RIGHT);
        rightButtonDown = YES;
    }
}

- (void)touchesBegan:(NSSet *)touches withEvent:(UIEvent *)event {
    NSUInteger touchCount = [[event allTouches] count];

    // Hybrid mode: one finger is absolute/direct touch, while two fingers
    // behave like a trackpad scroll gesture and send mouse-wheel events.
    if (touchCount == 2) {
        [self cancelDirectTouch];
        isTwoFingerScrolling = YES;
        lastTwoFingerLocation = [self centerOfTouches:[event allTouches]];
        return;
    }

    // Suppress one-finger input until every finger from a scroll gesture is lifted.
    // This also lets StreamView retain its existing three-finger keyboard gesture.
    if (touchCount > 1 || isTwoFingerScrolling) {
        return;
    }

    UITouch* touch = [touches anyObject];
    CGPoint touchLocation = [touch locationInView:view];

    // Don't reposition for finger down events within the deadzone. This makes double-clicking easier.
    if (touch.timestamp - lastTouchUp.timestamp > DOUBLE_TAP_DEAD_ZONE_DELAY ||
        sqrt(pow((touchLocation.x / view.bounds.size.width) - (lastTouchUpLocation.x / view.bounds.size.width), 2) +
             pow((touchLocation.y / view.bounds.size.height) - (lastTouchUpLocation.y / view.bounds.size.height), 2)) > DOUBLE_TAP_DEAD_ZONE_DELTA) {
        [view updateCursorLocation:touchLocation isMouse:NO];
    }

    // Delay the button press very slightly. If a second finger arrives during
    // this window, the gesture becomes scrolling without clicking the host.
    directTouchTimer = [NSTimer scheduledTimerWithTimeInterval:DIRECT_TOUCH_ACTIVATION_DELAY
                                                        target:self
                                                      selector:@selector(onDirectTouchStart:)
                                                      userInfo:nil
                                                       repeats:NO];

    longPressTimer = [NSTimer scheduledTimerWithTimeInterval:LONG_PRESS_ACTIVATION_DELAY
                                                       target:self
                                                     selector:@selector(onLongPressStart:)
                                                     userInfo:nil
                                                      repeats:NO];

    lastTouchDown = touch;
    lastTouchDownLocation = touchLocation;
}

- (void)touchesMoved:(NSSet *)touches withEvent:(UIEvent *)event {
    NSUInteger touchCount = [[event allTouches] count];

    if (isTwoFingerScrolling) {
        if (touchCount == 2) {
            CGPoint currentLocation = [self centerOfTouches:[event allTouches]];
            CGFloat deltaY = (currentLocation.y - lastTwoFingerLocation.y) * TWO_FINGER_SCROLL_MULTIPLIER;
            CGFloat deltaX = (currentLocation.x - lastTwoFingerLocation.x) * TWO_FINGER_SCROLL_MULTIPLIER;

            if (fabs(deltaY) >= 1.0f) {
                LiSendHighResScrollEvent((short)lrint(deltaY));
            }

            if (fabs(deltaX) >= 1.0f) {
                // Horizontal scrolling uses the opposite sign convention.
                LiSendHighResHScrollEvent((short)-lrint(deltaX));
            }

            lastTwoFingerLocation = currentLocation;
        }

        return;
    }

    if (touchCount > 1) {
        return;
    }

    UITouch* touch = [touches anyObject];
    CGPoint touchLocation = [touch locationInView:view];

    if (sqrt(pow((touchLocation.x / view.bounds.size.width) - (lastTouchDownLocation.x / view.bounds.size.width), 2) +
             pow((touchLocation.y / view.bounds.size.height) - (lastTouchDownLocation.y / view.bounds.size.height), 2)) > LONG_PRESS_ACTIVATION_DELTA) {
        // Moved too far since touch down. Cancel the long press timer.
        [longPressTimer invalidate];
        longPressTimer = nil;
    }

    // Movement means the user is dragging, so activate the left button now
    // rather than waiting for the short two-finger grace period to expire.
    [self activateLeftButton];
    [view updateCursorLocation:touchLocation isMouse:NO];
}

- (void)touchesEnded:(NSSet *)touches withEvent:(UIEvent *)event {
    NSUInteger totalTouchCount = [[event allTouches] count];
    NSUInteger endedTouchCount = [touches count];
    NSUInteger remainingTouchCount = totalTouchCount > endedTouchCount ? totalTouchCount - endedTouchCount : 0;

    if (isTwoFingerScrolling) {
        if (remainingTouchCount == 0) {
            isTwoFingerScrolling = NO;
            lastTwoFingerLocation = CGPointZero;
        }
        return;
    }

    // Only fire this logic if all touches have ended.
    if (remainingTouchCount == 0) {
        BOOL needsFastTap = directTouchTimer != nil && !leftButtonDown && !rightButtonDown;

        [self cancelTimers];

        // Very quick taps may finish before the grace timer. Send a complete click.
        if (needsFastTap) {
            LiSendMouseButtonEvent(BUTTON_ACTION_PRESS, BUTTON_LEFT);
            LiSendMouseButtonEvent(BUTTON_ACTION_RELEASE, BUTTON_LEFT);
        }
        else {
            [self releaseButtons];
        }

        // Remember this last touch for touch-down deadzoning.
        lastTouchUp = [touches anyObject];
        lastTouchUpLocation = [lastTouchUp locationInView:view];
    }
}

- (void)touchesCancelled:(NSSet *)touches withEvent:(UIEvent *)event {
    [self cancelDirectTouch];
    isTwoFingerScrolling = NO;
    lastTwoFingerLocation = CGPointZero;
}

@end
'''

OLD_FRAME_BLOCK = r'''    // Only enable scroll and zoom in absolute touch mode
    if (_settings.absoluteTouchMode) {
        _scrollView = [[UIScrollView alloc] initWithFrame:self.view.frame];
#if !TARGET_OS_TV
        [_scrollView.panGestureRecognizer setMinimumNumberOfTouches:2];
#endif
        [_scrollView setShowsHorizontalScrollIndicator:NO];
        [_scrollView setShowsVerticalScrollIndicator:NO];
        [_scrollView setDelegate:self];
        [_scrollView setMaximumZoomScale:10.0f];
        
        // Add StreamView inside a UIScrollView for absolute mode
        [_scrollView addSubview:_streamView];
        [self.view addSubview:_scrollView];
    }
    else {
        // Add StreamView directly in relative mode
        [self.view addSubview:_streamView];
    }
'''

NEW_FRAME_BLOCK = r'''    // Hybrid touch mode:
    // AbsoluteTouchHandler provides direct one-finger touch and forwards
    // two-finger movement as remote mouse-wheel scrolling. A UIScrollView
    // would steal that gesture for local pan/zoom, so attach StreamView directly.
    [self.view addSubview:_streamView];
'''


def fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


if not ABSOLUTE_PATH.exists() or not FRAME_PATH.exists():
    fail("Run this script from the root of the moonlight-ios repository.")

absolute_text = ABSOLUTE_PATH.read_text(encoding="utf-8")
frame_text = FRAME_PATH.read_text(encoding="utf-8")

if "TWO_FINGER_SCROLL_MULTIPLIER" in absolute_text:
    print("AbsoluteTouchHandler.m already appears to contain the hybrid-touch patch.")
else:
    if "@implementation AbsoluteTouchHandler" not in absolute_text:
        fail("AbsoluteTouchHandler.m does not look like the expected Moonlight source file.")
    ABSOLUTE_PATH.with_suffix(".m.bak").write_text(absolute_text, encoding="utf-8")
    ABSOLUTE_PATH.write_text(NEW_ABSOLUTE, encoding="utf-8")
    print(f"Patched {ABSOLUTE_PATH}")

if NEW_FRAME_BLOCK in frame_text:
    print("StreamFrameViewController.m already appears to contain the hybrid-touch patch.")
elif OLD_FRAME_BLOCK not in frame_text:
    fail("Could not find the expected absolute-touch UIScrollView block. Upstream source may have changed.")
else:
    FRAME_PATH.with_suffix(".m.bak").write_text(frame_text, encoding="utf-8")
    FRAME_PATH.write_text(frame_text.replace(OLD_FRAME_BLOCK, NEW_FRAME_BLOCK, 1), encoding="utf-8")
    print(f"Patched {FRAME_PATH}")

print("\nDone. Open Moonlight.xcodeproj, select your signing team, and build to your iPad.")
print("To undo: git checkout -- Limelight/Input/AbsoluteTouchHandler.m Limelight/ViewControllers/StreamFrameViewController.m")
