import actionlib
from actionlib_msgs.msg import GoalStatus
from threading import Lock

class MultiGoalActionServer(object):
    def __init__(self, server_name, action_spec, execute_cb, auto_start=True):
        self.action_server = actionlib.SimpleActionServer(
            server_name, action_spec, execute_cb=execute_cb, auto_start=False
        )
        self.goal_queue = []
        self.queue_lock = Lock()
        self.execute_cb = execute_cb
        self.action_server.register_goal_callback(self.goal_callback)
        self.action_server.register_preempt_callback(self.preempt_callback)

        if auto_start:
            self.action_server.start()

    def goal_callback(self):
        with self.queue_lock:
            if self.should_cancel_next_goal:
                self.action_server.set_aborted(
                    text="The goal has been canceled before processing"
                )
                self.should_cancel_next_goal = False
                return
            
            if not self.action_server.is_active():
                goal = self.action_server.accept_new_goal()
                self.goal_queue.append(goal)
                self.process_goal_queue()
            else:
                self.action_server.accept_new_goal()

    def preempt_callback(self):
        with self.queue_lock:
            if self.action_server.is_active():
                self.action_server.set_preempted(text="Goal preempted by client")
                self.cancel_remaining_goals()

    def process_goal_queue(self):
        while len(self.goal_queue) > 0:
            goal = self.goal_queue.pop(0)
            result = self.execute_cb(goal)
            if result == GoalStatus.ABORTED:
                self.action_server.set_aborted(text="Goal execution failed")
                self.cancel_remaining_goals()
                break
            elif result == GoalStatus.SUCCEEDED:
                self.action_server.set_succeeded(text="Goal execution succeeded")

    def cancel_remaining_goals(self):
        self.goal_queue = []

    def cancel_next_goal(self):
        self.should_cancel_next_goal = True

    def start(self):
        self.action_server.start()

    def __getattr__(self, name):
        return getattr(self.action_server, name)
