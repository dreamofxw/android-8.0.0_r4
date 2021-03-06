/*
 * Copyright (C) 2016 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package android.transition.cts;

import static org.mockito.Matchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

import android.support.test.filters.MediumTest;
import android.support.test.runner.AndroidJUnit4;
import android.transition.Fade;

import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;

/**
 * This tests the public API for Fade. The alpha cannot be easily tested as part of CTS,
 * so those are implementation tests.
 */
@MediumTest
@RunWith(AndroidJUnit4.class)
public class FadeTest extends BaseTransitionTest {
    Fade mFade;

    @Override
    @Before
    public void setup() {
        super.setup();
        resetTransition();
    }

    private void resetTransition() {
        mFade = new Fade();
        mFade.setDuration(200);
        mTransition = mFade;
        resetListener();
    }

    @Test
    public void testMode() throws Throwable {
        // Should animate in and out by default
        enterScene(R.layout.scene4);
        startTransition(R.layout.scene1);
        verify(mListener, never()).onTransitionEnd(any());
        waitForEnd(400);

        resetListener();
        startTransition(R.layout.scene4);
        verify(mListener, never()).onTransitionEnd(any());
        waitForEnd(400);

        // Now only animate in
        mFade = new Fade(Fade.IN);
        mTransition = mFade;
        resetListener();
        startTransition(R.layout.scene1);
        verify(mListener, never()).onTransitionEnd(any());
        waitForEnd(400);

        // No animation since it should only animate in
        resetListener();
        startTransition(R.layout.scene4);
        waitForEnd(0);

        // Now animate out, but no animation should happen since we're animating in.
        mFade = new Fade(Fade.OUT);
        mTransition = mFade;
        resetListener();
        startTransition(R.layout.scene1);
        waitForEnd(0);

        // but it should animate out
        resetListener();
        startTransition(R.layout.scene4);
        verify(mListener, never()).onTransitionEnd(any());
        waitForEnd(400);
    }
}

