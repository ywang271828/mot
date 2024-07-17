# -*- coding: utf-8 -*-
"""
Kalman class using opencv implementation
"""

import cv2 as cv
import numpy as np
from scipy.optimize import linear_sum_assignment
from xmot.mot.utils import cen2cor, cor2cen, costMatrix, unionBlob, iom

class Blob:
    """
    Abstraction of identified particles in video, (i.e. unqiue particle).

    Attributes:
        idx: integer Particle ID, starting from 1.
        bbox: [x1, y1, x2, y2] Coordinates of upper left and lower right corners.
        color: [x, y, z] RGB color code of the particle.
        dead: <TODO>
        frames: [<integer>] List of frame ids the particle lives in.
        kalm: CV KalmanFilter The kalmanfilter tracking this particle.
    """
    def __init__(self, idx, bbox, mask):
        self.idx    = idx
        self.bbox   = bbox
        self.masks  = [mask]
        self.color  = np.random.randint(0,255,size=(3,))
        self.dead   = 0
        self.frames = []  # Currently not used.

        # Kalman object
        self.kalm  = cv.KalmanFilter(8, 4, 0)

        # transition matrix
        F = np.array([[1, 0, 0, 0, 1, 0, 0, 0], # x
                      [0, 1, 0, 0, 0, 1, 0, 0], # y
                      [0, 0, 1, 0, 0, 0, 1, 0], # w
                      [0, 0, 0, 1, 0, 0, 0, 1], # h
                      [0, 0, 0, 0, 1, 0, 0, 0], # vx
                      [0, 0, 0, 0, 0, 1, 0, 0], # vy
                      [0, 0, 0, 0, 0, 0, 1, 0], # w_dot
                      [0, 0, 0, 0, 0, 0, 0, 1]  # h_dot
                      ], dtype=np.float32)

        self.kalm.transitionMatrix = F

        # measurement matrix
        self.kalm.measurementMatrix = np.eye(4, 8, dtype=np.float32)

        # process noise covariance
        self.kalm.processNoiseCov = 4.*np.eye(8, dtype=np.float32)

        # measurement noise covariance
        self.kalm.measurementNoiseCov = 4.*np.eye(4, dtype=np.float32)

        # Set posterior state
        state = list(cor2cen(self.bbox)) + [0, 0, 0, 0]
        self.kalm.statePost = np.array(state, dtype=np.float32)

    def predict(self):
        state = self.kalm.predict()
        self.bbox = np.array(cen2cor(state[0], state[1], state[2], state[3]))
        return state

    def correct(self, measurement, mask):
        # TODO: The bbox might not enclose the mask since the bbox has been modified by the Kalman filter.
        self.masks.append(mask)
        self.kalm.correct(measurement)

        # correct bbox with the state updated by Kalman from both estimation and measurement.
        state     = self.kalm.statePost
        self.bbox = np.array(cen2cor(state[0], state[1], state[2], state[3]))

    def statePost(self):
        return self.kalm.statePost

class MOT:

    # Number of frames allow for a blob to be undetected before dropping it from tracking.
    UNDETECTION_THRESHOLD = 2

    def __init__(self, bbox, mask, fixed_cost=100., merge=False, merge_it=2, merge_th=50):
        self.frame_id    = 0              # Current frame id
        self.blobs       = []             # List of currently tracked blobs (idenfied particle)
        self.blolen      = len(bbox)      # Total number of blobs currently tracked.
        self.blobs_all   = []             # List of all blobs, including deleted ones.
        self.total_blobs = 0
        self.fixed_cost  = fixed_cost     # Basic cost in the cost matrix for assignment problem.
        self.merge       = merge          # Flag: whether to merge bboxes
        self.merge_it    = merge_it       # Iteration to operate the merge
        self.merge_th    = merge_th

        # assign a blob for each box
        for i in range(self.blolen):
            # assign a blob for each bbox
            self.total_blobs += 1
            b = Blob(self.total_blobs, bbox[i], mask[i])
            b.frames.append(self.frame_id)
            self.blobs.append(b)
            self.blobs_all.append(b)

        # optional box merge
        # if merge:
        #    self.__merge()

    def step(self, bbox, mask):
        """
        Add bboxes of a frame and create/merge/delete blobs.
        """
        # advance frame_id
        self.frame_id += 1

        # make a prediction for each blob
        # Even for the blobs that haven't bee detected in last frame, but kept alive temporarily,
        # their position is updated by its Kalman Filter.
        self.__pred()

        # calculate cost and optimize using the Hungarian algo
        # When bbox has a length of zero (no particle in this frame), the assignment
        # retains its original order.
        blob_ind = self.__hungarian(bbox)

        # Update assigned blobs if exist. Otherwise, create new blobs
        new_blobs = self.__update(bbox, blob_ind, mask)  # Could be empty

        # Blobs to be deleted
        ind_del = self.__delBlobInd(bbox, blob_ind)

        # Delete blobs
        self.__delBlobs(ind_del)

        # Add new blobs
        self.blobs += new_blobs
        self.blobs_all += new_blobs
        self.total_blobs += len(new_blobs)

        # Optional merge
        # if self.merge:
        #    self.__merge()

        self.blolen = len(self.blobs)

    def __pred(self):
        # predict next position
        for i in range(self.blolen):
            self.blobs[i].predict()
            self.blobs[i].frames.append(self.frame_id) # Even include the "dead" frames.

    def __hungarian(self, bbox):
        """
        Return the ids of the existing blobs that matches the new bboxes in the new frame.
        """
        cost = costMatrix(bbox, self.blobs, fixed_cost=self.fixed_cost)
        # Default is to minimize the cost.
        box_ind, blob_ind = linear_sum_assignment(cost)
        return blob_ind

    def __update(self, bbox, blob_ind, mask):
        boxlen = len(bbox)
        new_blobs = []
        for i in range(boxlen):
            m   = np.array(cor2cen(bbox[i]), dtype=np.float32)
            ind = blob_ind[i]
            if ind < self.blolen:  # New bbox match one of the existing blob.
                self.blobs[ind].correct(m, mask[i])
                self.blobs[ind].dead = 0  # Recount the number of undetected frames.
            else:  # New bbox don't match any of the existing blob.
                # blob.idx starts from 1.
                b = Blob(self.total_blobs + len(new_blobs) + 1, bbox[i], mask[i])
                b.frames.append(self.frame_id)
                new_blobs.append(b)
        return new_blobs

    def __delBlobInd(self, bbox, blob_ind):
        # get unassigned blobs
        boxlen  = len(bbox)
        ind_del = []
        for i in range(boxlen, len(blob_ind)):
            if blob_ind[i] < boxlen:
                # Existing blob with blob_ind[i] does not match with any of the bboxes
                # in the new frame. Otherwise, blob_ind[i] should be the id of that new bbox,
                # which is smaller than boxlen.
                ind_del.append(blob_ind[i])

        return ind_del

    def __delBlobs(self, ind_del):
        # sort to start removing from the end
        ind_del.sort(reverse=True)
        for ind in ind_del:
            self.blobs[ind].dead += 1
            if self.blobs[ind].dead > MOT.UNDETECTION_THRESHOLD:
                #self.blobs_all.append(self.blobs[ind])
                self.blobs.pop(ind)

    def __merge(self):
        """
        (Deprecated) A bbox merge strategy based on location and velocity information from Kalman Filters.
        TODO: Handle the change of particle IDs when blobs are merged.
        """
        for i in range(self.merge_it):
            cursor_left  = 0
            cursor_right = 0
            length       = len(self.blobs)
            while(cursor_left < length):
                cursor_right = cursor_left + 1
                while(cursor_right < length):
                    # Get posterior states
                    state1    = self.blobs[cursor_left].statePost()
                    state2    = self.blobs[cursor_right].statePost()

                    # parse state vectors
                    cenx1,ceny1,w1,h1,vx1,vy1,_,_ = state1
                    cenx2,ceny2,w2,h2,vx2,vy2,_,_ = state2

                    # Metrics
                    dist    = np.sqrt( (cenx1-cenx2)**2 + (ceny1-ceny2)**2 )
                    dMetric = (dist**2)/(h1*w1) + (dist**2)/(h2*w2)
                    vMetric = np.sqrt( (vx1-vx2)**2 + (vy1-vy2)**2 )
                    iMetric = iom(self.blobs[cursor_left].bbox, self.blobs[cursor_right].bbox)

                    # merge
                    if vx1 == 0 and vx2 == 0 and vy1 == 0 and vy2 == 0:
                        mcon = iMetric>0.1
                    else:
                        mcon = (dMetric<1. or iMetric>0.05) and vMetric<2.
                        # mcon = (iMetric>0.05) and vMetric<1.

                    if mcon:
                        # merge blobs
                        blob1 = self.blobs[cursor_left]
                        blob2 = self.blobs[cursor_right]
                        self.blobs[cursor_left]  = unionBlob(blob1, blob2)

                        # pop merged data from lists
                        self.blobs.pop(cursor_right)
                        length = length - 1 # adjust length of the list
                    else:
                        cursor_right = cursor_right + 1
                cursor_left = cursor_left + 1

        # update blob length
        self.blolen = len(self.blobs)