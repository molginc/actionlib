import rospy
import threading
import traceback

from actionlib_msgs.msg import GoalStatus

from actionlib import ActionServer
from actionlib.server_goal_handle import ServerGoalHandle

from collections import deque

def nop_cb(goal_handle):
    pass


## @class MultiGoalActionServer
## @brief The MultiGoalActionServer
## implements a singe goal policy on top of the ActionServer class. The
## specification of the policy is as follows: only one goal can have an
## active status at a time, new goals preempt previous goals based on the
## stamp in their GoalID field (later goals preempt earlier ones), an
## explicit preempt goal preempts all goals with timestamps that are less
## than or equal to the stamp associated with the preempt, accepting a new
## goal implies successful preemption of any old goal and the status of the
## old goal will be change automatically to reflect this.
class MultiGoalActionServer:
    ## @brief Constructor for a MultiGoalActionServer
    ## @param name A name for the action server
    ## @param execute_cb Optional callback that gets called in a separate thread whenever
    ## a new goal is received, allowing users to have blocking callbacks.
    ## Adding an execute callback also deactivates the goalCallback.
    ## @param  auto_start A boolean value that tells the ActionServer wheteher or not to start publishing as soon as it comes up. THIS SHOULD ALWAYS BE SET TO FALSE TO AVOID RACE CONDITIONS and start() should be called after construction of the server.
    def __init__(self, name, ActionSpec, execute_cb=None, auto_start=True):

        self.new_goal = False
        self.preempt_request = False
        self.new_goal_preempt_request = False

        self.execute_callback = execute_cb
        self.goal_callback = None
        self.preempt_callback = None

        self.need_to_terminate = False
        self.terminate_mutex = threading.RLock()

        self.execution_queue = deque()

        # since the internal_goal/preempt_callbacks are invoked from the
        # ActionServer while holding the self.action_server.lock
        # self.lock must always be locked after the action server lock
        # to avoid an inconsistent lock acquisition order
        self.lock = threading.RLock()

        self.execute_condition = threading.Condition(self.lock)

        self.current_goal = ServerGoalHandle()
        self.next_goal = ServerGoalHandle()

        if self.execute_callback:
            self.execute_thread = threading.Thread(None, self.executeLoop)
            self.execute_thread.start()
        else:
            self.execute_thread = None

        # create the action server
        self.action_server = ActionServer(name, ActionSpec, self.internal_goal_callback, self.internal_preempt_callback, auto_start)


    def __del__(self):
        if hasattr(self, 'execute_callback') and self.execute_callback:
            with self.terminate_mutex:
                self.need_to_terminate = True

            assert(self.execute_thread)
            self.execute_thread.join()

    ## @brief Accepts a new goal when one is available The status of this
    ## goal is set to active upon acceptance, and the status of any
    ## previously active goal is set to preempted. Preempts received for the
    ## new goal between checking if isNewGoalAvailable or invokation of a
    ## goal callback and the acceptNewGoal call will not trigger a preempt
    ## callback.  This means, isPreemptReqauested should be called after
    ## accepting the goal even for callback-based implementations to make
    ## sure the new goal does not have a pending preempt request.
    ## @return A shared_ptr to the new goal.
    def accept_new_goal(self):
        with self.action_server.lock, self.lock:
            if not self.new_goal or not self.next_goal.get_goal():
                rospy.logerr("Attempting to accept the next goal when a new goal is not available")
                return None

            # check if we need to send a preempted message for the goal that we're currently pursuing
            if self.is_active() and self.current_goal.get_goal() and self.current_goal != self.next_goal:
                self.current_goal.set_canceled(None, "This goal was canceled because another goal was received by the simple action server")

            rospy.logdebug("Accepting a new goal")

            # accept the next goal
            self.current_goal = self.next_goal
            self.new_goal = False

            # set preempt to request to equal the preempt state of the new goal
            self.preempt_request = self.new_goal_preempt_request
            self.new_goal_preempt_request = False

            # set the status of the current goal to be active
            self.current_goal.set_accepted("This goal has been accepted by the simple action server")

            # return self.current_goal.get_goal()
            return

    def get_next_goal(self):
        # set the status of the current goal to be active

        if len(self.execution_queue)>0:
            self.next_goal = self.execution_queue[0]
            self.new_goal = True
            self.new_goal_preempt_request = False

            self.accept_new_goal()

            self.execution_queue.popleft()
            
        return self.current_goal.get_goal()

    ## @brief Allows  polling implementations to query about the availability of a new goal
    ## @return True if a new goal is available, false otherwise
    def is_new_goal_available(self):
        return self.new_goal

    ## @brief Allows  polling implementations to query about preempt requests
    ## @return True if a preempt is requested, false otherwise
    def is_preempt_requested(self):
        return self.preempt_request

    ## @brief Allows  polling implementations to query about the status of the current goal
    ## @return True if a goal is active, false otherwise
    def is_active(self):
        if not self.current_goal.get_goal():
            return False

        status = self.current_goal.get_goal_status().status
        return status == GoalStatus.ACTIVE or status == GoalStatus.PREEMPTING

    ## @brief Sets the status of the active goal to succeeded
    ## @param  result An optional result to send back to any clients of the goal
    def set_succeeded(self, result=None, text=""):
        with self.action_server.lock, self.lock:
            if not result:
                result = self.get_default_result()
            self.current_goal.set_succeeded(result, text)

    ## @brief Sets the status of the active goal to aborted
    ## @param  result An optional result to send back to any clients of the goal
    def set_aborted(self, result=None, text=""):
        with self.action_server.lock, self.lock:
            if not result:
                result = self.get_default_result()
            self.current_goal.set_aborted(result, text)
            # abort all goals in the execution queue
            self.reject_all("This goal was rejected and cleared from the execution queue.")

    ## @brief Publishes feedback for a given goal
    ## @param  feedback Shared pointer to the feedback to publish
    def publish_feedback(self, feedback):
        self.current_goal.publish_feedback(feedback)

    def get_default_result(self):
        return self.action_server.ActionResultType()

    ## @brief Sets the status of the active goal to preempted
    ## @param  result An optional result to send back to any clients of the goal
    def set_preempted(self, result=None, text=""):
        if not result:
            result = self.get_default_result()
        with self.action_server.lock, self.lock:
            rospy.logdebug("Setting the current goal as canceled")
            self.current_goal.set_canceled(result, text)
            # cancel all goals in the execution queue
            self.cancel_all("This goal was rejected and cleared from the execution queue.")

    ## @brief Sets the status of the all goals to rejected
    ## @param  text An optional text message to send back to any clients of the goal
    def reject_all(self, text=""):
        with self.action_server.lock, self.lock:
            # abort all goals in the execution queue
            while len(self.execution_queue)>0:
                self.execution_queue[0].set_rejected(None, text)
                self.execution_queue.popleft()

    ## @brief Sets the status of the all goals to recalled or preempted
    ## @param  text An optional text message to send back to any clients of the goal
    def cancel_all(self, text=""):
        with self.action_server.lock, self.lock:
            # abort all goals in the execution queue
            while len(self.execution_queue)>0:
                self.execution_queue[0].set_canceled(None, text)
                self.execution_queue.popleft()

    ## @brief Allows users to register a callback to be invoked when a new goal is available
    ## @param cb The callback to be invoked
    def register_goal_callback(self, cb):
        if self.execute_callback:
            rospy.logwarn("Cannot call MultiGoalActionServer.register_goal_callback() because an executeCallback exists. Not going to register it.")
        else:
            self.goal_callback = cb

    ## @brief Returns the number of goals currently in the queue
    def get_queue_size(self):
        return len(self.execution_queue)

    ## @brief Allows users to register a callback to be invoked when a new preempt request is available
    ## @param cb The callback to be invoked
    def register_preempt_callback(self, cb):
        self.preempt_callback = cb

    ## @brief Explicitly start the action server, used it auto_start is set to false
    def start(self):
        self.action_server.start()

    ## @brief Callback for when the ActionServer receives a new goal and passes it on
    def internal_goal_callback(self, goal):
        self.execute_condition.acquire()

        try:
            rospy.logdebug("A new goal %s has been received by the action server queue.", goal.get_goal_id().id)

            # check that the timestamp is past that of the current goal and the next goal
            if((not self.current_goal.get_goal() or goal.get_goal_id().stamp >= self.current_goal.get_goal_id().stamp)
               and (not self.next_goal.get_goal() or goal.get_goal_id().stamp >= self.next_goal.get_goal_id().stamp)):
                # if next_goal has not been accepted already... its going to get bumped, but we need to let the client know we're preempting
                # rospy.logdebug("Checking if the next goal has been accepted already...")
                # if(self.next_goal.get_goal() and (not self.current_goal.get_goal() or self.next_goal != self.current_goal)):
                #     self.next_goal.set_canceled(None, "This goal was canceled because another goal was received by the simple action server")

                rospy.logdebug("Appending new goal to execution queue")

                self.execution_queue.append(goal)

                # Trigger runLoop to call execute()
                self.execute_condition.notify()
                self.execute_condition.release()
            else:
                # the goal requested has already been preempted by a different goal, so we're not going to execute it
                goal.set_canceled(None, "This goal was canceled because another goal was received by the simple action server")
                self.execute_condition.release()
        except Exception as e:
            rospy.logerr("MultiGoalActionServer.internal_goal_callback - exception %s", str(e))
            self.execute_condition.release()

    ## @brief Callback for when the ActionServer receives a new preempt and passes it on
    def internal_preempt_callback(self, preempt):
        with self.lock:
            rospy.logdebug("A preempt has been received by the MultiGoalActionServer")

            # if the preempt is for the current goal, then we'll set the preemptRequest flag and call the user's preempt callback
            if(preempt == self.current_goal):
                rospy.logdebug("Setting preempt_request bit for the current goal to TRUE and invoking callback")
                self.preempt_request = True

                # if the user has registered a preempt callback, we'll call it now
                if(self.preempt_callback):
                    self.preempt_callback()
            # if the preempt applies to the next goal, we'll set the preempt bit for that
            elif(preempt == self.next_goal):
                rospy.logdebug("Setting preempt request bit for the next goal to TRUE")
                self.new_goal_preempt_request = True

    ## @brief Called from a separate thread to call blocking execute calls
    def executeLoop(self):
        loop_duration = rospy.Duration.from_sec(.1)

        while (not rospy.is_shutdown()):
            with self.terminate_mutex:
                if (self.need_to_terminate):
                    break

            # the following checks (is_active) are performed without locking
            # this checks if the action server is done with the current goal
            while (not rospy.is_shutdown()):
                if not (self.is_active()):
                    break

            # if (self.is_new_goal_available()):
                # accept_new_goal() is performing its own locking
                # self.accept_new_goal()
            goal = self.get_next_goal()

            if self.is_active():
                if not self.execute_callback:
                    rospy.logerr("execute_callback_ must exist. This is a bug in MultiGoalActionServer")
                    return

                try:
                    self.execute_callback(goal)

                    if self.is_active():
                        rospy.logwarn("Your executeCallback did not set the goal to a terminal status.  " +
                                      "This is a bug in your ActionServer implementation. Fix your code!  " +
                                      "For now, the ActionServer will set this goal to aborted")
                        self.set_aborted(None, "No terminal state was set.")
                except Exception as ex:
                    rospy.logerr("Exception in your execute callback: %s\n%s", str(ex),
                                    traceback.format_exc())
                    self.set_aborted(None, "Exception in execute callback: %s" % str(ex))

            with self.execute_condition:
                self.execute_condition.wait(loop_duration.to_sec())